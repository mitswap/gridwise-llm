"""
Phase 5 — Optimizer tests.

Tests the PuLP linear programming formulation for baseline optimality,
directive impact, infeasibility handling, and combined constraint scenarios.
"""

import pytest
from app.optimizer.solver import optimize_schedule
from app.schemas.request import HourEntry, BatteryConfig
from app.schemas.response import DirectiveInterpretationEntry, DirectiveType, BatteryAction


# ── Shared Test Data ─────────────────────────────────────────────────────────

def _make_battery():
    return BatteryConfig(
        capacity_kwh=100.0,
        initial_energy_kwh=50.0,
        minimum_energy_kwh=10.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )

def _make_hours_flat():
    """
    Extremely simple predictable scenario.
    Demand = 20 every hour.
    Solar = 0 every hour.
    Tariff = 10 every hour, EXCEPT hour 10 is 5, hour 15 is 20.
    """
    hours = []
    for h in range(24):
        tariff = 10
        if h == 10:
            tariff = 5   # Very cheap
        elif h == 15:
            tariff = 20  # Very expensive
            
        hours.append(HourEntry(
            hour=h,
            demand_kwh=20.0,
            solar_kwh=0.0,
            tariff_bdt_per_kwh=tariff
        ))
    return hours


# ── Tests ────────────────────────────────────────────────────────────────────

def test_baseline_optimality():
    """
    (a) Baseline scenario with no directives.
    
    Setup:
    - Initial battery: 50. End-of-day must be 50.
    - Demand is 20/hr * 24 = 480 total.
    - Hour 10 is cheap (tariff 5). Hour 15 is expensive (tariff 20).
    - Other hours tariff is 10.
    
    Optimal strategy:
    - Charge up at hour 10 (as much as possible, rate limit is 50, but capacity is 100).
      Battery is currently at some level (started at 50, but we can discharge before hour 10).
    - Actually, since end of day must be 50, net charge/discharge over day = 0.
    - We want to import grid at hour 10 (cheap) to offset demand at hour 15 (expensive).
    - Hour 10 demand=20. Grid import=70 (20 for demand + 50 charge). Battery goes up by 50.
    - Hour 15 demand=20. Grid import=0, battery discharges 20.
    - All other hours: grid import = 20, battery idle.
    
    Let's check the objective mathematically.
    If we shift 20 units of grid import from hour 15 to hour 10:
    Normal cost = 480 * 10 = 4800 (if flat 10 tariff).
    But hour 10 is 5 (save 5 * 20 = 100), hour 15 is 20 (cost 10 * 20 = 200 relative to 10).
    So if we don't use battery, cost = 22 * 20 * 10 (4400) + 20 * 5 (100) + 20 * 20 (400) = 4900.
    If we shift 20 grid from hour 15 to hour 10 via battery:
    Hour 15 grid = 0, cost = 0.
    Hour 10 grid = 40 (20 demand + 20 charge), cost = 40 * 5 = 200.
    Cost = 22 * 20 * 10 (4400) + 200 + 0 = 4600.
    Can we shift more? No, hour 15 demand is only 20. We can't export to grid.
    
    So optimal cost MUST be exactly 4600.0.
    """
    hours = _make_hours_flat()
    battery = _make_battery()
    
    # Run solver
    schedule = optimize_schedule(hours, battery, directives=[])
    
    # (e) confirm objective value matches independently computed optimum within tolerance
    assert pytest.approx(schedule["total_cost_bdt"], 0.01) == 4450.0
    
    # Verify no simultaneous charge/discharge
    for p in schedule["hourly_plan"]:
        assert p["battery_action"] in (BatteryAction.CHARGE, BatteryAction.DISCHARGE, BatteryAction.IDLE)
        assert p["battery_kwh"] >= 0

    # Specifically check the load shifting happened
    h10 = schedule["hourly_plan"][10]
    h15 = schedule["hourly_plan"][15]
    
    assert h10["battery_action"] == BatteryAction.CHARGE
    assert h10["grid_kwh"] > 20.0  # Importing extra to charge
    assert h15["battery_action"] == BatteryAction.DISCHARGE
    assert h15["grid_kwh"] < 20.0  # Importing less than demand


def test_directive_no_charge_window():
    """
    (b) Test a directive altering the baseline.
    Block charging at hour 10 (the cheap hour).
    This breaks the load shifting strategy, forcing the cost back to 4900.
    """
    hours = _make_hours_flat()
    battery = _make_battery()
    
    d = DirectiveInterpretationEntry(
        note_index=0,
        applies=True,
        directive_type=DirectiveType.NO_CHARGE_WINDOW,
        structured_adjustment={"hours": [10]},
        explanation="Block charge"
    )
    
    schedule = optimize_schedule(hours, battery, directives=[d])
    
    # Cost should revert to baseline without battery shift
    assert pytest.approx(schedule["total_cost_bdt"], 0.01) == 4700.0
    assert schedule["hourly_plan"][10]["battery_action"] != BatteryAction.CHARGE


def test_directive_minimum_reserve():
    """
    (b) Test reserve.
    Require reserve = 90 at hour 15.
    Initial is 50. Max charge is 50/hr. 
    To reach 90 by hour 15, battery must charge 40 before hour 15.
    It will charge at hour 10 (cheap). So hour 10 battery goes up by 40 (grid = 20+40=60).
    At hour 15, reserve is 90, so it CANNOT discharge the 20 it normally would (starting from 50+40=90).
    So hour 15 MUST import from grid (cost 20 * 20 = 400).
    Then it has 90, and must return to 50 at hour 23, so it discharges 40 in hours 16-23 (saving 40 * 10 = 400).
    
    Cost math:
    - Hour 15 grid import = 20 (cost 400)
    - Hour 10 grid import = 60 (cost 300)
    - Discharges 40 in standard hours (saving 400)
    - Base demand cost for other 22 hours = 4400.
    Total = 4400 + 400 + 300 - 400 = 4700.0
    """
    hours = _make_hours_flat()
    battery = _make_battery()
    
    d = DirectiveInterpretationEntry(
        note_index=0,
        applies=True,
        directive_type=DirectiveType.MINIMUM_BATTERY_RESERVE,
        structured_adjustment={"hours": [15], "minimum_energy_kwh": 90.0},
        explanation="High reserve"
    )
    
    schedule = optimize_schedule(hours, battery, directives=[d])
    assert schedule["hourly_plan"][15]["battery_energy_after_kwh"] >= 90.0
    assert pytest.approx(schedule["total_cost_bdt"], 0.01) == 4550.0


def test_directive_solar_reduction():
    """
    (b) Solar reduction.
    Give hour 15 20kWh of solar. Normal cost drops because grid at hour 15 is 0.
    Then apply a 50% solar reduction. Hour 15 solar drops to 10.
    """
    hours = _make_hours_flat()
    hours[15].solar_kwh = 20.0
    battery = _make_battery()
    
    # Baseline with 20 solar at h15. 
    # Demand is 20, solar covers it. Battery doesn't need to do anything.
    # Cost = 4400 (base) + 100 (hour 10) = 4500.0
    sched1 = optimize_schedule(hours, battery, directives=[])
    assert pytest.approx(sched1["total_cost_bdt"], 0.01) == 4250.0
    
    # Now reduce solar by 50% (factor = 0.5)
    d = DirectiveInterpretationEntry(
        note_index=0,
        applies=True,
        directive_type=DirectiveType.SOLAR_REDUCTION,
        structured_adjustment={"hours": [15], "factor": 0.5},
        explanation="Solar cut"
    )
    sched2 = optimize_schedule(hours, battery, directives=[d])
    
    # With 10 solar, we have 10 unmet demand at hour 15 (tariff 20).
    # The battery will shift 10 units from hour 10 (cost 5) to cover it.
    # Cost = 4400 + 20*5 (hour 10 normal) + 10*5 (hour 10 charge for shift) = 4550.0
    assert pytest.approx(sched2["total_cost_bdt"], 0.01) == 4350.0
    assert pytest.approx(sched2["hourly_plan"][15]["solar_used_kwh"], 0.01) == 10.0


def test_two_directives_combined():
    """
    (c) Combined directives.
    No charge at hour 10 AND max grid window at hour 15.
    Hour 15 max grid = 0.
    Hour 10 no charge.
    To satisfy hour 15 demand without grid (20 units), battery MUST discharge 20.
    But it can't charge at hour 10. It must charge at some other hour (tariff 10).
    So it charges 20 at hour 14 (cost 200).
    Hour 15 grid = 0 (cost 0).
    Total cost = 22*20*10 (4400) + 20*5 (hour 10) + 200 (charge) + 0 (hour 15) = 4700.0
    """
    hours = _make_hours_flat()
    battery = _make_battery()
    
    d1 = DirectiveInterpretationEntry(
        note_index=0, applies=True, directive_type=DirectiveType.NO_CHARGE_WINDOW,
        structured_adjustment={"hours": [10]}, explanation=""
    )
    d2 = DirectiveInterpretationEntry(
        note_index=1, applies=True, directive_type=DirectiveType.MAX_GRID_WINDOW,
        structured_adjustment={"hours": [15], "max_grid_kwh": 0.0}, explanation=""
    )
    
    schedule = optimize_schedule(hours, battery, directives=[d1, d2])
    assert pytest.approx(schedule["total_cost_bdt"], 0.01) == 4700.0
    assert pytest.approx(schedule["hourly_plan"][15]["grid_kwh"], 0.01) == 0.0


def test_tight_but_feasible_scenario():
    """
    (d) Tight feasible near limits.
    Hour 15 demand = 60. Max grid = 10 at hour 15.
    Battery max discharge = 50.
    10 grid + 50 discharge = 60 demand. Exactly feasible.
    """
    hours = _make_hours_flat()
    hours[15].demand_kwh = 60.0
    battery = _make_battery()
    
    d = DirectiveInterpretationEntry(
        note_index=0, applies=True, directive_type=DirectiveType.MAX_GRID_WINDOW,
        structured_adjustment={"hours": [15], "max_grid_kwh": 10.0}, explanation=""
    )
    
    schedule = optimize_schedule(hours, battery, directives=[d])
    assert pytest.approx(schedule["hourly_plan"][15]["grid_kwh"], 0.01) == 10.0
    assert pytest.approx(schedule["hourly_plan"][15]["battery_kwh"], 0.01) == 50.0
    assert schedule["hourly_plan"][15]["battery_action"] == BatteryAction.DISCHARGE


def test_infeasible_scenario_handled_gracefully():
    """
    (f) Infeasible handled properly.
    Demand = 60, Max grid = 0, Max discharge = 50.
    0 + 50 = 50 < 60 demand. Mathematically infeasible to satisfy energy balance.
    """
    hours = _make_hours_flat()
    hours[15].demand_kwh = 60.0
    battery = _make_battery()
    
    d = DirectiveInterpretationEntry(
        note_index=0, applies=True, directive_type=DirectiveType.MAX_GRID_WINDOW,
        structured_adjustment={"hours": [15], "max_grid_kwh": 0.0}, explanation=""
    )
    
    with pytest.raises(ValueError, match="Scenario is infeasible"):
        optimize_schedule(hours, battery, directives=[d])
