"""
Phase 2 — Round-trip serialization tests and valid payload parsing.

Verifies:
  - Valid Problem Statement payload parses cleanly through Pydantic
  - Request model → dict → Request model round-trips losslessly
  - Response model serialization matches the contract schema
  - Directive types and battery actions serialize as expected strings
"""

import copy
import pytest

from app.schemas.request import OptimizeEnergyRequest, HourEntry, BatteryConfig
from app.schemas.response import (
    OptimizeEnergyResponse,
    DirectiveInterpretationEntry,
    HourlyPlanEntry,
    DirectiveType,
    BatteryAction,
)
from tests.conftest import make_valid_payload


# ═══════════════════════════════════════════════════════════════════════════
# REQUEST — valid parsing
# ═══════════════════════════════════════════════════════════════════════════

class TestRequestParsing:
    """Valid payload from the Problem Statement must parse cleanly."""

    def test_valid_payload_parses(self, valid_payload):
        req = OptimizeEnergyRequest(**valid_payload)
        assert req.scenario_id == "GRID-101"
        assert len(req.operator_notes) == 3
        assert len(req.hours) == 24
        assert req.hours[0].hour == 0
        assert req.hours[23].hour == 23
        assert req.battery.capacity_kwh == 500
        assert req.battery.initial_energy_kwh == 200

    def test_single_note_valid(self, valid_payload):
        valid_payload["operator_notes"] = ["One note only."]
        req = OptimizeEnergyRequest(**valid_payload)
        assert len(req.operator_notes) == 1

    def test_two_notes_valid(self, valid_payload):
        valid_payload["operator_notes"] = ["Note one.", "Note two."]
        req = OptimizeEnergyRequest(**valid_payload)
        assert len(req.operator_notes) == 2

    def test_hours_sorted_after_parsing(self, valid_payload):
        """Even if hours are sent out of order, they should be sorted."""
        import random
        random.shuffle(valid_payload["hours"])
        req = OptimizeEnergyRequest(**valid_payload)
        for i, entry in enumerate(req.hours):
            assert entry.hour == i

    def test_zero_solar_and_demand_valid(self, valid_payload):
        """Edge case: all zeros should be fine."""
        for h in valid_payload["hours"]:
            h["solar_kwh"] = 0
            h["demand_kwh"] = 0
            h["tariff_bdt_per_kwh"] = 0
        valid_payload["battery"]["initial_energy_kwh"] = 50
        req = OptimizeEnergyRequest(**valid_payload)
        assert all(entry.demand_kwh == 0 for entry in req.hours)

    def test_battery_at_limits(self, valid_payload):
        """Battery initial == capacity == minimum is valid."""
        valid_payload["battery"]["capacity_kwh"] = 100
        valid_payload["battery"]["initial_energy_kwh"] = 100
        valid_payload["battery"]["minimum_energy_kwh"] = 100
        req = OptimizeEnergyRequest(**valid_payload)
        assert req.battery.capacity_kwh == 100


# ═══════════════════════════════════════════════════════════════════════════
# REQUEST — round-trip serialization
# ═══════════════════════════════════════════════════════════════════════════

class TestRequestRoundTrip:
    """Request model → dict → model round-trips losslessly."""

    def test_model_to_dict_to_model(self, valid_payload):
        req1 = OptimizeEnergyRequest(**valid_payload)
        as_dict = req1.model_dump()
        req2 = OptimizeEnergyRequest(**as_dict)

        assert req2.scenario_id == req1.scenario_id
        assert req2.operator_notes == req1.operator_notes
        assert len(req2.hours) == len(req1.hours)
        for h1, h2 in zip(req1.hours, req2.hours):
            assert h1.hour == h2.hour
            assert h1.demand_kwh == h2.demand_kwh
            assert h1.solar_kwh == h2.solar_kwh
            assert h1.tariff_bdt_per_kwh == h2.tariff_bdt_per_kwh
        assert req2.battery.capacity_kwh == req1.battery.capacity_kwh

    def test_json_roundtrip(self, valid_payload):
        req1 = OptimizeEnergyRequest(**valid_payload)
        json_str = req1.model_dump_json()
        req2 = OptimizeEnergyRequest.model_validate_json(json_str)
        assert req2.scenario_id == req1.scenario_id
        assert req2.battery.capacity_kwh == req1.battery.capacity_kwh


# ═══════════════════════════════════════════════════════════════════════════
# RESPONSE — serialization
# ═══════════════════════════════════════════════════════════════════════════

class TestResponseSerialization:
    """Response models serialize to the exact contract schema."""

    def _make_response(self) -> OptimizeEnergyResponse:
        """Build a minimal valid response for serialization testing."""
        directives = [
            DirectiveInterpretationEntry(
                note_index=0,
                applies=True,
                directive_type=DirectiveType.SOLAR_REDUCTION,
                structured_adjustment={"hours": [13, 14], "factor": 0.2},
                explanation="Solar reduced during maintenance.",
            ),
            DirectiveInterpretationEntry(
                note_index=1,
                applies=False,
                directive_type=DirectiveType.NO_OP,
                structured_adjustment=None,
                explanation="Not relevant to energy schedule.",
            ),
        ]
        hourly_plan = [
            HourlyPlanEntry(
                hour=h,
                grid_kwh=200,
                solar_used_kwh=0,
                battery_action=BatteryAction.IDLE,
                battery_kwh=0,
                battery_energy_after_kwh=200,
            )
            for h in range(24)
        ]
        return OptimizeEnergyResponse(
            scenario_id="GRID-TEST",
            directive_interpretation=directives,
            hourly_plan=hourly_plan,
            total_grid_kwh=4800,
            total_cost_bdt=43200,
            peak_grid_kwh=200,
            plan_summary="Baseline schedule with no optimization.",
        )

    def test_response_has_all_fields(self):
        resp = self._make_response()
        data = resp.model_dump()
        assert "scenario_id" in data
        assert "directive_interpretation" in data
        assert "hourly_plan" in data
        assert "total_grid_kwh" in data
        assert "total_cost_bdt" in data
        assert "peak_grid_kwh" in data
        assert "plan_summary" in data

    def test_directive_type_serializes_as_string(self):
        resp = self._make_response()
        data = resp.model_dump()
        assert data["directive_interpretation"][0]["directive_type"] == "solar_reduction"
        assert data["directive_interpretation"][1]["directive_type"] == "no_op"

    def test_battery_action_serializes_as_string(self):
        resp = self._make_response()
        data = resp.model_dump()
        assert data["hourly_plan"][0]["battery_action"] == "idle"

    def test_no_op_has_null_adjustment(self):
        resp = self._make_response()
        data = resp.model_dump()
        no_op = data["directive_interpretation"][1]
        assert no_op["applies"] is False
        assert no_op["structured_adjustment"] is None

    def test_applies_true_for_non_noop(self):
        resp = self._make_response()
        data = resp.model_dump()
        solar = data["directive_interpretation"][0]
        assert solar["applies"] is True
        assert solar["structured_adjustment"] is not None

    def test_hourly_plan_has_24_entries(self):
        resp = self._make_response()
        data = resp.model_dump()
        assert len(data["hourly_plan"]) == 24
        hours = [e["hour"] for e in data["hourly_plan"]]
        assert hours == list(range(24))

    def test_response_json_roundtrip(self):
        resp1 = self._make_response()
        json_str = resp1.model_dump_json()
        resp2 = OptimizeEnergyResponse.model_validate_json(json_str)
        assert resp2.scenario_id == resp1.scenario_id
        assert resp2.total_cost_bdt == resp1.total_cost_bdt
        assert len(resp2.directive_interpretation) == len(resp1.directive_interpretation)


# ═══════════════════════════════════════════════════════════════════════════
# DIRECTIVE TYPE ENUM
# ═══════════════════════════════════════════════════════════════════════════

class TestDirectiveTypeEnum:
    """All 6 supported directive types exist and serialize correctly."""

    @pytest.mark.parametrize("dtype", [
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
        "no_op",
    ])
    def test_all_types_exist(self, dtype):
        dt = DirectiveType(dtype)
        assert dt.value == dtype

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError):
            DirectiveType("invented_directive")
