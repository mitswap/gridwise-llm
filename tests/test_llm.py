"""
Phase 3 — LLM Interpreter and Fallback Tests.

Tests the offline rule-based fallback interpreter against 20+ paraphrases
(4 examples from the Problem Statement + 16 custom ones) to ensure high
accuracy (≥90%) on classification and parameter extraction.

Also tests the LLM retry-with-repair logic, parsing resilience, and
timeout/fallback mechanisms using a mocked OpenAI client.
"""

import json
import pytest
from typing import Any
from unittest.mock import AsyncMock, patch, MagicMock

from app.llm.fallback import interpret_notes_offline
from app.llm.interpreter import (
    interpret_operator_notes,
    _parse_llm_response,
    _strip_markdown_fences,
    reset_client,
)
from app.schemas.request import HourEntry, BatteryConfig


# ═══════════════════════════════════════════════════════════════════════════
# 20+ PARAPHRASE TEST SET (Offline Fallback Evaluation)
# ═══════════════════════════════════════════════════════════════════════════
# The fallback interpreter provides robust baseline handling without an LLM.
# We test its accuracy across diverse phrasings.

PARAPHRASE_TESTS = [
    # ── Problem Statement Examples (4) ──
    {
        "note": "Solar output will drop to about 20% from 1 PM to 3 PM.",
        "expected_type": "solar_reduction",
        "expected_hours": [13, 14],
        "expected_factor": 0.20,
    },
    {
        "note": "Do not charge the battery between 2 PM and 4 PM.",
        "expected_type": "no_charge_window",
        "expected_hours": [14, 15],
    },
    {
        "note": "Maintain a minimum battery reserve of 50 kWh from 6 PM until 9 PM.",
        "expected_type": "minimum_battery_reserve",
        "expected_hours": [18, 19, 20],
        "expected_kwh": 50,
    },
    {
        "note": "The cafeteria menu changes tomorrow.",
        "expected_type": "no_op",
    },

    # ── Custom Paraphrases: solar_reduction ──
    {
        "note": "Heavy cloud cover expected between 10 am and noon, expect solar to decrease by 40%.",
        "expected_type": "solar_reduction",
        "expected_hours": [10, 11],
        "expected_factor": 0.60,  # 1 - 0.40
    },
    {
        "note": "PV panels being cleaned from 8 AM to 11 AM, output drops to 50%.",
        "expected_type": "solar_reduction",
        "expected_hours": [8, 9, 10],
        "expected_factor": 0.50,
    },
    {
        "note": "Rooftop solar efficiency only 15% from 4 PM to 6 PM due to shadows.",
        "expected_type": "solar_reduction",
        "expected_hours": [16, 17],
        "expected_factor": 0.15,
    },

    # ── Custom Paraphrases: no_charge_window ──
    {
        "note": "Suspend all charging between midnight and 3 AM.",
        "expected_type": "no_charge_window",
        "expected_hours": [0, 1, 2],
    },
    {
        "note": "Battery charging is prohibited from 18:00 to 20:00.",
        "expected_type": "no_charge_window",
        "expected_hours": [18, 19],
    },
    {
        "note": "Halt battery charge cycles between 5 PM and 7 PM.",
        "expected_type": "no_charge_window",
        "expected_hours": [17, 18],
    },

    # ── Custom Paraphrases: no_discharge_window ──
    {
        "note": "Do not discharge the battery between 8 AM and 10 AM.",
        "expected_type": "no_discharge_window",
        "expected_hours": [8, 9],
    },
    {
        "note": "Prevent discharging from 14:00 to 16:00.",
        "expected_type": "no_discharge_window",
        "expected_hours": [14, 15],
    },
    {
        "note": "No energy should be drawn from storage between 7 AM and 9 AM.",
        "expected_type": "no_discharge_window",
        "expected_hours": [7, 8],
    },

    # ── Custom Paraphrases: minimum_battery_reserve ──
    {
        "note": "Keep at least 100 kWh in the battery between 4 PM and 8 PM.",
        "expected_type": "minimum_battery_reserve",
        "expected_hours": [16, 17, 18, 19],
        "expected_kwh": 100,
    },
    {
        "note": "Ensure battery reserve does not fall below 75 kWh from 20:00 to 23:00.",
        "expected_type": "minimum_battery_reserve",
        "expected_hours": [20, 21, 22],
        "expected_kwh": 75,
    },
    {
        "note": "Must have 120 kWh backup storage between 10 AM and 2 PM.",
        "expected_type": "minimum_battery_reserve",
        "expected_hours": [10, 11, 12, 13],
        "expected_kwh": 120,
    },

    # ── Custom Paraphrases: max_grid_window ──
    {
        "note": "Cap grid import at 250 kWh between 1 PM and 5 PM.",
        "expected_type": "max_grid_window",
        "expected_hours": [13, 14, 15, 16],
        "expected_kwh": 250,
    },
    {
        "note": "Grid usage must not exceed 100 kWh from 5 PM to 7 PM.",
        "expected_type": "max_grid_window",
        "expected_hours": [17, 18],
        "expected_kwh": 100,
    },
    {
        "note": "Limit grid to 50.5 kWh between 08:00 and 10:00.",
        "expected_type": "max_grid_window",
        "expected_hours": [8, 9],
        "expected_kwh": 50.5,
    },

    # ── Custom Paraphrases: no_op / Distractors ──
    {
        "note": "Security staff shift change at 3 PM.",
        "expected_type": "no_op",
    },
    {
        "note": "Main building HVAC maintenance tomorrow, no impact on demand.",
        "expected_type": "no_op",
    },
    {
        "note": "Guest lecture in auditorium starting at 6 PM.",
        "expected_type": "no_op",
    },
    {
        "note": "Remember to submit the weekly energy report by Friday.",
        "expected_type": "no_op",
    },
    # ── Extra Paraphrases (expanding to >40 total) ──
    {"note": "Cloudy day, solar efficiency only 90% from 2 PM to 3 PM.", "expected_type": "solar_reduction", "expected_hours": [14], "expected_factor": 0.90},
    {"note": "Solar output will drop to about 0% from 10 AM to 11 AM.", "expected_type": "solar_reduction", "expected_hours": [10], "expected_factor": 0.0},
    {"note": "Panels are dirty, output falls to 80% from 8 AM to 11 AM.", "expected_type": "solar_reduction", "expected_hours": [8, 9, 10], "expected_factor": 0.80},
    {"note": "Expect only 30% solar yield between 15:00 and 17:00.", "expected_type": "solar_reduction", "expected_hours": [15, 16], "expected_factor": 0.30},

    {"note": "Do not charge battery from 6 AM to 9 AM.", "expected_type": "no_charge_window", "expected_hours": [6, 7, 8]},
    {"note": "Stop charging at 11:00 until 12:00.", "expected_type": "no_charge_window", "expected_hours": [11]},
    {"note": "Halt battery charge cycles from 21:00 to 23:00.", "expected_type": "no_charge_window", "expected_hours": [21, 22]},
    {"note": "No charging between 10 AM and noon.", "expected_type": "no_charge_window", "expected_hours": [10, 11]},

    {"note": "Do not discharge battery from 5 AM to 6 AM.", "expected_type": "no_discharge_window", "expected_hours": [5]},
    {"note": "Prevent discharging between 1 PM and 3 PM.", "expected_type": "no_discharge_window", "expected_hours": [13, 14]},
    {"note": "No energy should be drawn from storage from 19:00 to 21:00.", "expected_type": "no_discharge_window", "expected_hours": [19, 20]},
    {"note": "Prevent any discharge from midnight to 2 AM.", "expected_type": "no_discharge_window", "expected_hours": [0, 1]},

    {"note": "Hold at least 20 kWh from 9 AM to 11 AM.", "expected_type": "minimum_battery_reserve", "expected_hours": [9, 10], "expected_kwh": 20},
    {"note": "Reserve 45.5 kWh between 13:00 and 16:00.", "expected_type": "minimum_battery_reserve", "expected_hours": [13, 14, 15], "expected_kwh": 45.5},
    {"note": "Maintain 200 kWh backup from 2 PM to 5 PM.", "expected_type": "minimum_battery_reserve", "expected_hours": [14, 15, 16], "expected_kwh": 200},
    
    {"note": "Grid import limit 15 kWh between 6 AM and 8 AM.", "expected_type": "max_grid_window", "expected_hours": [6, 7], "expected_kwh": 15},
    {"note": "Cap grid import at 0 kWh from 22:00 to 23:00.", "expected_type": "max_grid_window", "expected_hours": [22], "expected_kwh": 0},
    {"note": "Cap grid at 400 kWh from 10 AM to 1 PM.", "expected_type": "max_grid_window", "expected_hours": [10, 11, 12], "expected_kwh": 400},
    
    {"note": "The CEO is visiting at 2 PM.", "expected_type": "no_op"},
    {"note": "Ensure lights are on in lobby.", "expected_type": "no_op"},
    {"note": "Water the plants tomorrow.", "expected_type": "no_op"},
]


class TestOfflineFallbackParaphrases:
    """Evaluate offline rule-based fallback against 20 paraphrases."""

    @pytest.mark.parametrize("case", PARAPHRASE_TESTS, ids=lambda c: c["note"][:30])
    def test_paraphrase_accuracy(self, case):
        note = case["note"]
        results = interpret_notes_offline([note])
        res = results[0]

        # Directive type match
        assert res["directive_type"] == case["expected_type"]
        
        # Applies boolean
        if case["expected_type"] == "no_op":
            assert res["applies"] is False
            assert res["structured_adjustment"] is None
        else:
            assert res["applies"] is True
            adj = res["structured_adjustment"]
            assert adj is not None
            
            # Key fields check
            if "expected_hours" in case:
                assert set(adj["hours"]) == set(case["expected_hours"])
            if "expected_factor" in case:
                assert pytest.approx(adj["factor"], 0.01) == case["expected_factor"]
            if "expected_kwh" in case:
                if "minimum_energy_kwh" in adj:
                    assert adj["minimum_energy_kwh"] == case["expected_kwh"]
                elif "max_grid_kwh" in adj:
                    assert adj["max_grid_kwh"] == case["expected_kwh"]


# ═══════════════════════════════════════════════════════════════════════════
# LLM INTERPRETER UNIT TESTS (Mocked LLM)
# ═══════════════════════════════════════════════════════════════════════════

class TestLLMInterpreterLogic:
    """Test JSON parsing, fence stripping, retry logic, and fallback triggering."""

    def test_strip_markdown_fences(self):
        # Clean JSON
        assert _strip_markdown_fences('{"a": 1}') == '{"a": 1}'
        # Fenced JSON
        fenced = "```json\n{\"a\": 1}\n```"
        assert _strip_markdown_fences(fenced) == '{"a": 1}'
        # Fenced without 'json'
        fenced2 = "```\n{\"b\": 2}\n```"
        assert _strip_markdown_fences(fenced2) == '{"b": 2}'

    def test_parse_llm_response_direct_array(self):
        raw = '[{"note_index": 0, "directive_type": "no_op"}]'
        parsed = _parse_llm_response(raw, 1)
        assert len(parsed) == 1
        assert parsed[0]["directive_type"] == "no_op"

    def test_parse_llm_response_wrapped_object(self):
        raw = '{"interpretations": [{"note_index": 0, "directive_type": "solar_reduction"}]}'
        parsed = _parse_llm_response(raw, 1)
        assert len(parsed) == 1
        assert parsed[0]["directive_type"] == "solar_reduction"

    def test_parse_llm_response_missing_note_index(self):
        raw = '[{"directive_type": "no_op"}]'
        parsed = _parse_llm_response(raw, 1)
        assert parsed[0]["note_index"] == 0  # auto-injected

    def test_parse_llm_response_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_response("this is not json", 1)

    def test_parse_llm_response_not_an_array(self):
        with pytest.raises(ValueError, match="JSON object has no recognizable array key"):
            _parse_llm_response('{"just": "a dict"}', 1)

    @pytest.mark.asyncio
    @patch("app.llm.interpreter.get_openai_client")
    @patch("app.llm.interpreter.OPENAI_API_KEY", "dummy-key")
    async def test_llm_success_first_try(self, mock_get_client):
        """Valid JSON on the first try."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"interpretations": [{"note_index": 0, "directive_type": "no_op"}]}'
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        hours = []
        battery = BatteryConfig(capacity_kwh=100, initial_energy_kwh=50, minimum_energy_kwh=10, max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50)
        
        result = await interpret_operator_notes(["Just a note"], hours, battery)
        
        assert len(result) == 1
        assert result[0]["directive_type"] == "no_op"
        # API called exactly once
        assert mock_client.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    @patch("app.llm.interpreter.get_openai_client")
    @patch("app.llm.interpreter.OPENAI_API_KEY", "dummy-key")
    async def test_llm_retry_on_bad_json_then_success(self, mock_get_client):
        """First try invalid JSON, second try valid JSON (repair logic)."""
        mock_client = AsyncMock()
        
        bad_response = MagicMock()
        bad_response.choices = [MagicMock()]
        bad_response.choices[0].message.content = "```plain\nOops no json here\n```"
        
        good_response = MagicMock()
        good_response.choices = [MagicMock()]
        good_response.choices[0].message.content = '[{"note_index": 0, "directive_type": "no_op"}]'
        
        # Side effect: first call returns bad, second returns good
        mock_client.chat.completions.create.side_effect = [bad_response, good_response]
        mock_get_client.return_value = mock_client

        hours = []
        battery = BatteryConfig(capacity_kwh=100, initial_energy_kwh=50, minimum_energy_kwh=10, max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50)
        
        result = await interpret_operator_notes(["Fix this"], hours, battery)
        
        assert len(result) == 1
        assert result[0]["directive_type"] == "no_op"
        # API called exactly twice (attempt + repair)
        assert mock_client.chat.completions.create.call_count == 2

    @pytest.mark.asyncio
    @patch("app.llm.interpreter.get_openai_client")
    @patch("app.llm.interpreter.OPENAI_API_KEY", "dummy-key")
    async def test_llm_fallback_after_failed_retry(self, mock_get_client):
        """First and second try invalid JSON -> fallback to offline."""
        mock_client = AsyncMock()
        bad_response = MagicMock()
        bad_response.choices = [MagicMock()]
        bad_response.choices[0].message.content = "Still not json"
        
        mock_client.chat.completions.create.return_value = bad_response
        mock_get_client.return_value = mock_client

        hours = []
        battery = BatteryConfig(capacity_kwh=100, initial_energy_kwh=50, minimum_energy_kwh=10, max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50)
        
        # "menu changes" should trigger offline fallback's no_op
        result = await interpret_operator_notes(["The cafeteria menu changes tomorrow."], hours, battery)
        
        assert len(result) == 1
        assert result[0]["directive_type"] == "no_op"
        assert result[0]["explanation"] == "Note does not match any supported energy directive."
        assert mock_client.chat.completions.create.call_count == 2

    @pytest.mark.asyncio
    @patch("app.llm.interpreter.OPENAI_API_KEY", "")
    async def test_immediate_fallback_if_no_api_key(self):
        """If no API key, bypass LLM entirely and use offline fallback."""
        hours = []
        battery = BatteryConfig(capacity_kwh=100, initial_energy_kwh=50, minimum_energy_kwh=10, max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50)
        
        # This paraphrase maps to offline fallback "no_charge_window"
        result = await interpret_operator_notes(["Do not charge the battery between 2 PM and 4 PM."], hours, battery)
        
        assert len(result) == 1
        assert result[0]["directive_type"] == "no_charge_window"
        assert result[0]["structured_adjustment"]["hours"] == [14, 15]
