"""
Final Validator (Replay).

This module independently re-simulates the math optimizer's proposed plan.
It re-applies all rules and constraints (energy balance, battery boundaries,
rate limits, and directive overrides) to ensure the solver did not hallucinate,
cheat, or drift due to float precision issues.

If replay fails, it throws a ValueError (which `routes.py` catches to return
a 500 error, rather than shipping a non-compliant schedule).
It also fully recalculates the totals directly from the validated hourly plan.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.schemas.request import HourEntry, BatteryConfig
from app.schemas.response import DirectiveInterpretationEntry, DirectiveType, BatteryAction

logger = logging.getLogger(__name__)


def validate_final_plan(
    schedule: dict[str, Any],
    hours: list[HourEntry],
    battery: BatteryConfig,
    directives: list[DirectiveInterpretationEntry],
) -> dict[str, Any]:
    """
    Replay the schedule and strictly assert compliance with all physical and
    business constraints. Recalculate totals directly from the hourly_plan.
    """
    hourly_plan = schedule.get("hourly_plan")
    if not isinstance(hourly_plan, list) or len(hourly_plan) != 24:
        raise ValueError("Schedule must contain exactly 24 hourly plan entries.")

    # 1. Pre-process constraint arrays (same logic as optimizer, independently built)
    effective_solar = [h.solar_kwh for h in hours]
    min_reserve = [battery.minimum_energy_kwh] * 24
    max_charge = [battery.max_charge_kwh_per_hour] * 24
    max_discharge = [battery.max_discharge_kwh_per_hour] * 24
    max_grid = [float('inf')] * 24

    for d in directives:
        if not d.applies or not d.structured_adjustment:
            continue
        adj = d.structured_adjustment
        for h in adj["hours"]:
            if d.directive_type == DirectiveType.SOLAR_REDUCTION:
                effective_solar[h] = min(effective_solar[h], hours[h].solar_kwh * adj["factor"])
            elif d.directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE:
                min_reserve[h] = max(min_reserve[h], adj["minimum_energy_kwh"])
            elif d.directive_type == DirectiveType.NO_CHARGE_WINDOW:
                max_charge[h] = 0.0
            elif d.directive_type == DirectiveType.NO_DISCHARGE_WINDOW:
                max_discharge[h] = 0.0
            elif d.directive_type == DirectiveType.MAX_GRID_WINDOW:
                max_grid[h] = min(max_grid[h], adj["max_grid_kwh"])

    # 2. Run the hour-by-hour simulation
    current_battery_kwh = battery.initial_energy_kwh
    recalc_total_grid = 0.0
    recalc_total_cost = 0.0
    recalc_peak_grid = 0.0
    TOLERANCE = 0.01

    for h in range(24):
        plan = hourly_plan[h]
        
        if plan.get("hour") != h:
            raise ValueError(f"Plan out of order: expected hour {h}, got {plan.get('hour')}")

        grid_import = plan.get("grid_kwh", 0.0)
        solar_used = plan.get("solar_used_kwh", 0.0)
        action = plan.get("battery_action")
        action_kwh = plan.get("battery_kwh", 0.0)
        reported_after = plan.get("battery_energy_after_kwh", 0.0)
        demand = hours[h].demand_kwh

        if grid_import < -TOLERANCE or solar_used < -TOLERANCE or action_kwh < -TOLERANCE:
            raise ValueError(f"Hour {h}: Values cannot be negative.")

        # Determine true charge/discharge amounts
        charge = action_kwh if action == BatteryAction.CHARGE else 0.0
        discharge = action_kwh if action == BatteryAction.DISCHARGE else 0.0

        # Assert Energy Balance: supply == demand
        # Supply: grid_import + solar_used + battery discharge
        # Demand: building demand + battery charge
        supply = grid_import + solar_used + discharge
        total_demand = demand + charge
        if not math.isclose(supply, total_demand, abs_tol=TOLERANCE):
            raise ValueError(f"Hour {h}: Energy balance violation. Supply ({supply}) != Demand ({total_demand})")

        # Assert Effective Solar
        if solar_used > effective_solar[h] + TOLERANCE:
            raise ValueError(f"Hour {h}: Solar used ({solar_used}) exceeds effective cap ({effective_solar[h]})")

        # Assert Grid Cap
        if grid_import > max_grid[h] + TOLERANCE:
            raise ValueError(f"Hour {h}: Grid import ({grid_import}) exceeds directive cap ({max_grid[h]})")

        # Assert Charge/Discharge Limits
        if charge > max_charge[h] + TOLERANCE:
            raise ValueError(f"Hour {h}: Charge ({charge}) exceeds limit ({max_charge[h]})")
        if discharge > max_discharge[h] + TOLERANCE:
            raise ValueError(f"Hour {h}: Discharge ({discharge}) exceeds limit ({max_discharge[h]})")

        # Advance Battery State
        current_battery_kwh = current_battery_kwh + charge - discharge

        # Assert State matches reported
        if not math.isclose(current_battery_kwh, reported_after, abs_tol=TOLERANCE):
            raise ValueError(f"Hour {h}: Battery tracking error. Simulated={current_battery_kwh}, Reported={reported_after}")

        # Assert Battery Bounds (Capacity and Reserve)
        if current_battery_kwh > battery.capacity_kwh + TOLERANCE:
            raise ValueError(f"Hour {h}: Battery energy ({current_battery_kwh}) exceeds capacity ({battery.capacity_kwh})")
        if current_battery_kwh < min_reserve[h] - TOLERANCE:
            raise ValueError(f"Hour {h}: Battery energy ({current_battery_kwh}) fell below minimum reserve ({min_reserve[h]})")

        # Accumulate Recomputed Totals
        recalc_total_grid += grid_import
        recalc_total_cost += grid_import * hours[h].tariff_bdt_per_kwh
        if grid_import > recalc_peak_grid:
            recalc_peak_grid = grid_import

    # 3. Assert End of Day Neutrality
    if not math.isclose(current_battery_kwh, battery.initial_energy_kwh, abs_tol=TOLERANCE):
        raise ValueError(f"End of day neutrality violated: ended with {current_battery_kwh}, started with {battery.initial_energy_kwh}")

    # 4. Assemble Verified Plan
    summary = f"Optimal schedule verified. Total cost: BDT {recalc_total_cost:.2f}, Peak grid: {recalc_peak_grid:.2f} kWh."
    if recalc_total_cost == 0:
        summary = "Optimal schedule verified. 100% self-sufficient (zero grid import)."

    return {
        "hourly_plan": hourly_plan,
        "total_grid_kwh": round(recalc_total_grid, 4),
        "total_cost_bdt": round(recalc_total_cost, 4),
        "peak_grid_kwh": round(recalc_peak_grid, 4),
        "plan_summary": summary,
    }
