"""
Math Optimizer — builds and solves the 24-hour energy scheduling LP.

Uses PuLP to formulate:
  minimize  sum(grid_kwh[h] * tariff[h])  for h in 0..23

subject to:
  - Energy balance each hour
  - Battery state transitions and bounds
  - Charge / discharge rate limits
  - Effective solar limits (after solar_reduction)
  - no_charge_window / no_discharge_window constraints
  - minimum_battery_reserve constraints
  - max_grid_window constraints
  - End-of-day battery neutrality
"""

from __future__ import annotations

import logging
from typing import Any
import warnings
import pulp

# Suppress PuLP 4.0 deprecation warnings to keep logs clean
warnings.filterwarnings("ignore", category=DeprecationWarning, module="pulp")

from app.schemas.request import HourEntry, BatteryConfig
from app.schemas.response import DirectiveInterpretationEntry, DirectiveType, BatteryAction

logger = logging.getLogger(__name__)


def optimize_schedule(
    hours: list[HourEntry],
    battery: BatteryConfig,
    directives: list[DirectiveInterpretationEntry],
) -> dict[str, Any]:
    """
    Build and solve the 24-hour energy optimization LP using PuLP.
    """
    # ── 1. Pre-process Directives ────────────────────────────────────────────
    # Build hour-by-hour arrays for dynamic limits
    effective_solar = [h.solar_kwh for h in hours]
    min_reserve = [battery.minimum_energy_kwh] * 24
    max_charge = [battery.max_charge_kwh_per_hour] * 24
    max_discharge = [battery.max_discharge_kwh_per_hour] * 24
    max_grid = [None] * 24  # None means unconstrained (practically infinite)

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
                # If multiple overlapping, take the strictest (lowest) cap
                current = max_grid[h]
                if current is None:
                    max_grid[h] = adj["max_grid_kwh"]
                else:
                    max_grid[h] = min(current, adj["max_grid_kwh"])

    # ── 2. Build the LP Model ────────────────────────────────────────────────
    model = pulp.LpProblem("GridWise_Energy_Optimization", pulp.LpMinimize)

    # Decision variables for each hour (0 to 23)
    grid_vars = []
    solar_vars = []
    charge_vars = []
    discharge_vars = []
    energy_vars = []

    for h in range(24):
        # Grid import: >= 0, capped if max_grid_window applies
        up_bound = max_grid[h] if max_grid[h] is not None else None
        grid_vars.append(pulp.LpVariable(f"grid_{h}", lowBound=0, upBound=up_bound, cat="Continuous"))
        
        # Solar used: cannot exceed effective solar available that hour
        solar_vars.append(pulp.LpVariable(f"solar_{h}", lowBound=0, upBound=effective_solar[h], cat="Continuous"))
        
        # Battery charge/discharge bounded by rate limits
        charge_vars.append(pulp.LpVariable(f"charge_{h}", lowBound=0, upBound=max_charge[h], cat="Continuous"))
        discharge_vars.append(pulp.LpVariable(f"discharge_{h}", lowBound=0, upBound=max_discharge[h], cat="Continuous"))
        
        # Battery energy state bounded by capacity and reserve
        energy_vars.append(pulp.LpVariable(f"energy_{h}", lowBound=min_reserve[h], upBound=battery.capacity_kwh, cat="Continuous"))

    # Objective: Minimize total cost (grid import * tariff)
    model += pulp.lpSum(grid_vars[h] * hours[h].tariff_bdt_per_kwh for h in range(24)), "Total_Cost"

    # Constraints
    for h in range(24):
        # 1. Energy Balance: supply == demand + charge
        # supply: grid + solar_used + discharge
        # demand: hourly_demand + charge
        model += (
            grid_vars[h] + solar_vars[h] + discharge_vars[h] 
            == hours[h].demand_kwh + charge_vars[h]
        ), f"Energy_Balance_{h}"

        # 2. Battery State Evolution
        if h == 0:
            prev_energy = battery.initial_energy_kwh
        else:
            prev_energy = energy_vars[h-1]
            
        model += (
            energy_vars[h] == prev_energy + charge_vars[h] - discharge_vars[h]
        ), f"Battery_Evolution_{h}"

    # 3. End-of-day battery neutrality
    # Must end the day with exactly what it started with.
    model += (energy_vars[23] == battery.initial_energy_kwh), "Battery_Neutrality"

    # ── 3. Solve ─────────────────────────────────────────────────────────────
    # Use PuLP's default CBC solver, suppress output
    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=10)
    model.solve(solver)

    status = pulp.LpStatus[model.status]
    if status != "Optimal":
        logger.error("LP Solver failed: status=%s", status)
        raise ValueError(f"Scenario is infeasible (Solver status: {status})")

    # ── 4. Post-process & Format ─────────────────────────────────────────────
    hourly_plan = []
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0

    for h in range(24):
        g_val = grid_vars[h].varValue or 0.0
        s_val = solar_vars[h].varValue or 0.0
        c_val = charge_vars[h].varValue or 0.0
        d_val = discharge_vars[h].varValue or 0.0
        e_val = energy_vars[h].varValue or 0.0

        # Tie-break: if both charge and discharge are mathematically positive (e.g., 
        # both 10 to bleed off excess solar at 0 cost), net them out so exactly one action is taken.
        net = c_val - d_val
        final_charge = max(0.0, net)
        final_discharge = max(0.0, -net)
        
        # Small tolerance for float drift
        if final_charge > 1e-4:
            action = BatteryAction.CHARGE
            action_kwh = final_charge
        elif final_discharge > 1e-4:
            action = BatteryAction.DISCHARGE
            action_kwh = final_discharge
        else:
            action = BatteryAction.IDLE
            action_kwh = 0.0

        hourly_plan.append({
            "hour": h,
            "grid_kwh": round(g_val, 4),
            "solar_used_kwh": round(s_val, 4),
            "battery_action": action,
            "battery_kwh": round(action_kwh, 4),
            "battery_energy_after_kwh": round(e_val, 4),
        })

        total_grid += g_val
        total_cost += g_val * hours[h].tariff_bdt_per_kwh
        if g_val > peak_grid:
            peak_grid = g_val

    # Construct plan summary dynamically based on cost and peak
    summary = f"Optimal schedule generated. Total cost: BDT {total_cost:.2f}, Peak grid: {peak_grid:.2f} kWh."
    if total_cost == 0:
        summary = "Optimal schedule generated. 100% self-sufficient (zero grid import)."

    return {
        "hourly_plan": hourly_plan,
        "total_grid_kwh": round(total_grid, 4),
        "total_cost_bdt": round(total_cost, 4),
        "peak_grid_kwh": round(peak_grid, 4),
        "plan_summary": summary,
    }
