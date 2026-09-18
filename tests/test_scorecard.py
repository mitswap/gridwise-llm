"""
Phase 9 — Scorecard & Held-Out Test Bank

Evaluates the offline fallback and/or LLM against a completely fresh,
held-out set of 20+ paraphrases designed to catch overfitting.
It also processes the official Public Sample Case end-to-end.
"""

import json
import pytest
from httpx import AsyncClient, ASGITransport
from typing import Any

from app.main import app
from tests.conftest import make_valid_payload
from app.llm.fallback import interpret_notes_offline
from app.optimizer.solver import optimize_schedule
from app.guardrails.validator import validate_directives


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Held-Out Paraphrase Set (20+) ────────────────────────────────────────────

HELD_OUT_TESTS = [
    # solar_reduction
    {"note": "Thick smog warning: PV generation is expected to drop by 25% between 12:00 and 14:00.", "expected_type": "solar_reduction", "expected_hours": [12, 13], "expected_factor": 0.75},
    {"note": "Due to dust storm, solar panels will operate at half capacity from 3 PM to 5 PM.", "expected_type": "solar_reduction", "expected_hours": [15, 16], "expected_factor": 0.50},
    {"note": "Expect zero solar contribution at 9 AM.", "expected_type": "solar_reduction", "expected_hours": [9], "expected_factor": 0.0},
    {"note": "Rooftop arrays undergoing repairs; output drops to 60% from 8 AM to 10 AM.", "expected_type": "solar_reduction", "expected_hours": [8, 9], "expected_factor": 0.60},
    
    # no_charge_window
    {"note": "Cease all battery charging activities from 11 PM to 1 AM.", "expected_type": "no_charge_window", "expected_hours": [23, 0]},
    {"note": "Do not allow the storage unit to charge between 14:00 and 16:00.", "expected_type": "no_charge_window", "expected_hours": [14, 15]},
    {"note": "Charging the battery is strictly forbidden at 8 AM.", "expected_type": "no_charge_window", "expected_hours": [8]},
    {"note": "Halt battery intake from 5 PM to 7 PM.", "expected_type": "no_charge_window", "expected_hours": [17, 18]},

    # no_discharge_window
    {"note": "Preserve battery state: do not discharge between 10 AM and 11 AM.", "expected_type": "no_discharge_window", "expected_hours": [10]},
    {"note": "Storage cannot be drained from 16:00 to 18:00.", "expected_type": "no_discharge_window", "expected_hours": [16, 17]},
    {"note": "Ban discharging from midnight to 4 AM.", "expected_type": "no_discharge_window", "expected_hours": [0, 1, 2, 3]},
    {"note": "Ensure battery provides zero output from 6 PM to 7 PM.", "expected_type": "no_discharge_window", "expected_hours": [18]},

    # minimum_battery_reserve
    {"note": "Emergency protocol: maintain a 60 kWh buffer in the battery from 18:00 to 22:00.", "expected_type": "minimum_battery_reserve", "expected_hours": [18, 19, 20, 21], "expected_kwh": 60.0},
    {"note": "Keep 85.5 kWh reserved for critical loads between 2 PM and 4 PM.", "expected_type": "minimum_battery_reserve", "expected_hours": [14, 15], "expected_kwh": 85.5},
    {"note": "Minimum storage capacity must stay above 25 kWh from 8 AM to noon.", "expected_type": "minimum_battery_reserve", "expected_hours": [8, 9, 10, 11], "expected_kwh": 25.0},
    {"note": "Reserve 10 kWh all morning from 6 AM to 9 AM.", "expected_type": "minimum_battery_reserve", "expected_hours": [6, 7, 8], "expected_kwh": 10.0},

    # max_grid_window
    {"note": "To avoid penalties, cap external grid draw at 150 kWh between 17:00 and 20:00.", "expected_type": "max_grid_window", "expected_hours": [17, 18, 19], "expected_kwh": 150.0},
    {"note": "Limit grid import to absolute zero from 1 PM to 2 PM.", "expected_type": "max_grid_window", "expected_hours": [13], "expected_kwh": 0.0},
    {"note": "Do not import more than 40 kWh at 5 AM.", "expected_type": "max_grid_window", "expected_hours": [5], "expected_kwh": 40.0},
    {"note": "Grid restriction: max 12.5 kWh from 9 AM to 11 AM.", "expected_type": "max_grid_window", "expected_hours": [9, 10], "expected_kwh": 12.5},

    # no_op (distractors)
    {"note": "The fire alarm system is being tested at 10 AM.", "expected_type": "no_op"},
    {"note": "Janitorial staff will be cleaning the solar panels tonight.", "expected_type": "no_op"},
    {"note": "VIP arrival expected tomorrow morning, but demand remains steady.", "expected_type": "no_op"},
    {"note": "System upgrade scheduled for the billing software at 2 PM.", "expected_type": "no_op"},
]


class TestScorecard:
    """Internal scoreboard tracking held-out interpretation & application."""

    def test_held_out_accuracy(self):
        """Measures accuracy on the held-out validation set (simulating unseen hidden tests)."""
        correct = 0
        total = len(HELD_OUT_TESTS)
        
        # Test just the interpretation engine (we use the offline rule-based fallback
        # here for deterministic speed, though LLM should be even better).
        for case in HELD_OUT_TESTS:
            try:
                res = interpret_notes_offline([case["note"]])[0]
                # Compare type
                if res["directive_type"] == case["expected_type"]:
                    correct += 1
            except Exception:
                pass
                
        accuracy = (correct / total) * 100
        print(f"\n[SCORECARD] Held-Out Interpretation Accuracy: {accuracy:.1f}% ({correct}/{total})")
        # Ensure we haven't completely broken the fallback engine.
        # Fallback regex is tough to get 100% on wild paraphrases, but expect >50%
        assert accuracy > 50.0, "Held-out accuracy dropped below acceptable threshold (overfitting detected)"


    @pytest.mark.asyncio
    async def test_public_sample_cases(self, client):
        """End-to-end processing of the official BUP sample cases."""
        import json
        import os
        
        file_path = r"e:\BUP\BUP_CSE_FEST_2026_Participant_Docs\BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
        if not os.path.exists(file_path):
            pytest.skip(f"Public sample cases file not found at {file_path}")
            
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        cases = data.get("cases", [])
        assert len(cases) > 0, "No cases found in public sample JSON"
        
        for case in cases:
            sample_payload = case["input"]
            expected = case["expected_output"]
            
            resp = await client.post("/optimize-energy", json=sample_payload)
            assert resp.status_code == 200, f"Sample case {case['id']} failed: {resp.text}"
            
            result = resp.json()
            
            # Verify schema elements
            assert "scenario_id" in result
            assert "directive_interpretation" in result
            assert "hourly_plan" in result
            assert len(result["hourly_plan"]) == 24
            
            # Since the ReplayValidator ran successfully on the server side (200 OK),
            # we know the constraints were physically satisfied.
            # We just do a sanity check on cost.
            # "Equivalent optimal schedules are accepted... Absolute differences up to 0.01 BDT are treated as equivalent."
            expected_cost = expected["total_cost_bdt"]
            actual_cost = result["total_cost_bdt"]
            
            assert abs(actual_cost - expected_cost) <= 0.01, f"Cost mismatch for {case['id']}: expected {expected_cost}, got {actual_cost}"
            
            print(f"\n[SCORECARD] Public Sample Case {case['id']}: 100% Passed. Total Cost: BDT {actual_cost:.2f}")
    # End of test_public_sample_cases
