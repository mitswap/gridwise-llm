"""
Phase 6 — Final Validator (Replay) tests.

Ensures that the replay validator correctly verifies all mathematical constraints
and catches any attempts to cheat (energy balance violations, battery bound 
violations, etc.)
"""

import pytest
from app.validator.replay import validate_final_plan
from app.optimizer.solver import optimize_schedule
from app.schemas.request import HourEntry, BatteryConfig
from app.schemas.response import DirectiveInterpretationEntry, DirectiveType, BatteryAction


# ── Shared Data ──────────────────────────────────────────────────────────────

def _make_battery():
    return BatteryConfig(
        capacity_kwh=100.0,
        initial_energy_kwh=50.0,
        minimum_energy_kwh=10.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )

def _make_hours():
    hours = []
    for h in range(24):
        hours.append(HourEntry(
            hour=h,
            demand_kwh=20.0,
            solar_kwh=10.0,
            tariff_bdt_per_kwh=10.0
        ))
    return hours


# ── Tests ────────────────────────────────────────────────────────────────────

def test_valid_schedule_passes():
    """A mathematically optimal schedule from Phase 5 should pass replay cleanly."""
    hours = _make_hours()
    battery = _make_battery()
    
    # Baseline schedule
    schedule = optimize_schedule(hours, battery, [])
    
    # Replay it
    validated = validate_final_plan(schedule, hours, battery, [])
    
    # Check that totals were recomputed and match
    assert validated["total_cost_bdt"] == schedule["total_cost_bdt"]
    assert len(validated["hourly_plan"]) == 24


def test_energy_balance_violation_caught():
    """If a schedule claims to use less grid than physically required, it should fail."""
    hours = _make_hours()
    battery = _make_battery()
    schedule = optimize_schedule(hours, battery, [])
    
    # Cheat: Reduce grid import by 5 kWh at hour 10 without changing anything else
    schedule["hourly_plan"][10]["grid_kwh"] -= 5.0
    
    with pytest.raises(ValueError, match="Energy balance violation"):
        validate_final_plan(schedule, hours, battery, [])


def test_battery_bounds_violation_caught():
    """If a schedule claims the battery has more energy than capacity, it should fail."""
    hours = _make_hours()
    battery = _make_battery()
    schedule = optimize_schedule(hours, battery, [])
    
    # Cheat: Claim the battery has 110 kWh at hour 5
    schedule["hourly_plan"][5]["battery_energy_after_kwh"] = 110.0
    
    with pytest.raises(ValueError, match="Battery tracking error"):
        validate_final_plan(schedule, hours, battery, [])


def test_directive_violation_caught():
    """If a schedule violates an active directive, it should fail."""
    hours = _make_hours()
    battery = _make_battery()
    
    # Directive: Max grid 0 at hour 15
    d = DirectiveInterpretationEntry(
        note_index=0, applies=True, directive_type=DirectiveType.MAX_GRID_WINDOW,
        structured_adjustment={"hours": [15], "max_grid_kwh": 0.0}, explanation=""
    )
    
    schedule = optimize_schedule(hours, battery, [d])
    
    # Cheat: Inject 5 kWh grid import at hour 15, and reduce solar to balance it
    schedule["hourly_plan"][15]["grid_kwh"] += 5.0
    schedule["hourly_plan"][15]["solar_used_kwh"] -= 5.0
    
    with pytest.raises(ValueError, match="exceeds directive cap"):
        validate_final_plan(schedule, hours, battery, [d])


def test_end_of_day_neutrality_violation_caught():
    """If the battery doesn't return to its starting state, it should fail."""
    hours = _make_hours()
    battery = _make_battery()
    schedule = optimize_schedule(hours, battery, [])
    
    # Cheat: At hour 23, say it ended with 40 kWh instead of 50
    schedule["hourly_plan"][23]["battery_energy_after_kwh"] = 40.0
    
    with pytest.raises(ValueError, match="Battery tracking error"):
        validate_final_plan(schedule, hours, battery, [])
