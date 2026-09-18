"""
Response schemas — exact match to the Problem Statement Section 10.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DirectiveType(str, Enum):
    """All supported directive types (Section 04)."""
    SOLAR_REDUCTION = "solar_reduction"
    MINIMUM_BATTERY_RESERVE = "minimum_battery_reserve"
    NO_CHARGE_WINDOW = "no_charge_window"
    NO_DISCHARGE_WINDOW = "no_discharge_window"
    MAX_GRID_WINDOW = "max_grid_window"
    NO_OP = "no_op"


class BatteryAction(str, Enum):
    """Exactly one of charge, discharge, idle."""
    CHARGE = "charge"
    DISCHARGE = "discharge"
    IDLE = "idle"


class DirectiveInterpretationEntry(BaseModel):
    """One entry per operator note, in note_index order (Section 10.2)."""
    note_index: int = Field(..., ge=0)
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: dict[str, Any] | None = None
    explanation: str


class HourlyPlanEntry(BaseModel):
    """One plan entry per hour 0–23 (Section 10.3)."""
    hour: int = Field(..., ge=0, le=23)
    grid_kwh: float = Field(..., ge=0)
    solar_used_kwh: float = Field(..., ge=0)
    battery_action: BatteryAction
    battery_kwh: float = Field(..., ge=0)
    battery_energy_after_kwh: float = Field(..., ge=0)


class OptimizeEnergyResponse(BaseModel):
    """POST /optimize-energy response body (Section 10.1)."""
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretationEntry]
    hourly_plan: list[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
