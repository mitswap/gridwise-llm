"""
Phase 4 — Guardrail Validator Tests.

Exhaustive tests to ensure 100% of fuzzed malformed LLM outputs are safely
neutralized to no_op without crashing the service, and valid outputs pass unchanged.
"""

import math
import pytest
from app.guardrails.validator import validate_directives
from app.schemas.request import BatteryConfig
from app.schemas.response import DirectiveType


@pytest.fixture
def battery():
    return BatteryConfig(
        capacity_kwh=100.0,
        initial_energy_kwh=50.0,
        minimum_energy_kwh=10.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )


def test_valid_pass_through_unchanged(battery):
    raw = [
        {
            "note_index": 0,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [10, 11, 12], "factor": 0.5},
            "explanation": "Valid solar reduction"
        },
        {
            "note_index": 1,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [18, 19], "minimum_energy_kwh": 30.0},
            "explanation": "Valid reserve"
        }
    ]
    
    validated = validate_directives(raw, 2, battery)
    
    assert len(validated) == 2
    assert validated[0].note_index == 0
    assert validated[0].directive_type == DirectiveType.SOLAR_REDUCTION
    assert validated[0].structured_adjustment["factor"] == 0.5
    assert validated[0].structured_adjustment["hours"] == [10, 11, 12]
    assert validated[0].applies is True
    
    assert validated[1].note_index == 1
    assert validated[1].directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE
    assert validated[1].structured_adjustment["minimum_energy_kwh"] == 30.0


def test_missing_note_index_filled_with_noop(battery):
    # Only index 1 is provided for a 3-note scenario
    raw = [
        {
            "note_index": 1,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [1, 2]},
        }
    ]
    validated = validate_directives(raw, 3, battery)
    assert len(validated) == 3
    assert validated[0].directive_type == DirectiveType.NO_OP
    assert validated[0].note_index == 0
    assert validated[1].directive_type == DirectiveType.NO_CHARGE_WINDOW
    assert validated[1].note_index == 1
    assert validated[2].directive_type == DirectiveType.NO_OP
    assert validated[2].note_index == 2


def test_duplicate_note_index_dropped(battery):
    raw = [
        {"note_index": 0, "directive_type": "no_charge_window", "structured_adjustment": {"hours": [1]}},
        {"note_index": 0, "directive_type": "max_grid_window", "structured_adjustment": {"hours": [2], "max_grid_kwh": 10}},
    ]
    validated = validate_directives(raw, 1, battery)
    assert len(validated) == 1
    assert validated[0].directive_type == DirectiveType.NO_CHARGE_WINDOW


def test_invalid_directive_type_demoted(battery):
    raw = [
        {
            "note_index": 0,
            "directive_type": "magic_invented_rule",
            "structured_adjustment": {"hours": [1]}
        }
    ]
    validated = validate_directives(raw, 1, battery)
    assert validated[0].directive_type == DirectiveType.NO_OP
    assert "Unsupported directive_type" in validated[0].explanation


def test_no_op_enforces_null_adjustment(battery):
    raw = [
        {
            "note_index": 0,
            "directive_type": "no_op",
            "structured_adjustment": {"sneaky": "data"}
        }
    ]
    validated = validate_directives(raw, 1, battery)
    assert validated[0].directive_type == DirectiveType.NO_OP
    assert validated[0].applies is False
    assert validated[0].structured_adjustment is None


def test_hours_deduplication_and_sorting(battery):
    raw = [
        {
            "note_index": 0,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [15, 15, 2, 8, 2]}
        }
    ]
    validated = validate_directives(raw, 1, battery)
    assert validated[0].structured_adjustment["hours"] == [2, 8, 15]


def test_hours_out_of_range_dropped(battery):
    raw = [
        {
            "note_index": 0,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [-1, 0, 24, 23, 99]}
        }
    ]
    validated = validate_directives(raw, 1, battery)
    assert validated[0].structured_adjustment["hours"] == [0, 23]


def test_hours_completely_invalid_demoted(battery):
    raw = [
        {
            "note_index": 0,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [-5, 25, "not an int"]}
        }
    ]
    validated = validate_directives(raw, 1, battery)
    assert validated[0].directive_type == DirectiveType.NO_OP
    assert "No valid hours" in validated[0].explanation


def test_solar_factor_clamped(battery):
    raw = [
        {
            "note_index": 0,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [1], "factor": 1.5}  # Too high
        },
        {
            "note_index": 1,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [1], "factor": -0.5}  # Too low
        }
    ]
    validated = validate_directives(raw, 2, battery)
    assert validated[0].structured_adjustment["factor"] == 1.0
    assert validated[1].structured_adjustment["factor"] == 0.0


def test_solar_factor_nan_infinity_demoted(battery):
    raw = [
        {
            "note_index": 0,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [1], "factor": math.nan}
        },
        {
            "note_index": 1,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [1], "factor": math.inf}
        }
    ]
    validated = validate_directives(raw, 2, battery)
    assert validated[0].directive_type == DirectiveType.NO_OP
    assert validated[1].directive_type == DirectiveType.NO_OP


def test_battery_reserve_capped_at_capacity(battery):
    raw = [
        {
            "note_index": 0,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [1], "minimum_energy_kwh": 150.0} # Battery max is 100
        }
    ]
    validated = validate_directives(raw, 1, battery)
    assert validated[0].structured_adjustment["minimum_energy_kwh"] == 100.0


def test_battery_reserve_negative_demoted(battery):
    raw = [
        {
            "note_index": 0,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [1], "minimum_energy_kwh": -10.0}
        }
    ]
    validated = validate_directives(raw, 1, battery)
    assert validated[0].directive_type == DirectiveType.NO_OP


def test_max_grid_negative_demoted(battery):
    raw = [
        {
            "note_index": 0,
            "directive_type": "max_grid_window",
            "structured_adjustment": {"hours": [1], "max_grid_kwh": -50.0}
        }
    ]
    validated = validate_directives(raw, 1, battery)
    assert validated[0].directive_type == DirectiveType.NO_OP


def test_malformed_wrapper_demoted(battery):
    raw = [
        "this is just a string, not a dict",
        None,
        {"note_index": 2, "directive_type": "solar_reduction", "structured_adjustment": "not a dict"}
    ]
    validated = validate_directives(raw, 3, battery)
    assert len(validated) == 3
    for v in validated:
        assert v.directive_type == DirectiveType.NO_OP


def test_hallucinated_fields_stripped(battery):
    """Ensure LLM cannot rewrite demand or tariffs by hallucinating fields."""
    raw = [
        {
            "note_index": 0,
            "directive_type": "no_charge_window",
            "structured_adjustment": {
                "hours": [5, 6],
                "demand_kwh": 999,  # Hallucinated
                "tariff_bdt_per_kwh": 0 # Hallucinated
            }
        }
    ]
    validated = validate_directives(raw, 1, battery)
    adj = validated[0].structured_adjustment
    assert "hours" in adj
    assert "demand_kwh" not in adj
    assert "tariff_bdt_per_kwh" not in adj
