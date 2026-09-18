"""
Request schemas — exact match to the Problem Statement Section 07.

All numeric fields must be finite (rejects NaN, Inf, -Inf).
"""

from __future__ import annotations

import math
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict


class HourEntry(BaseModel):
    """One of the 24 hourly entries (Section 07.2)."""
    model_config = ConfigDict(extra="forbid")

    hour: int = Field(..., ge=0, le=23)
    demand_kwh: float = Field(..., ge=0)
    solar_kwh: float = Field(..., ge=0)
    tariff_bdt_per_kwh: float = Field(..., ge=0)

    @model_validator(mode="after")
    def check_finite(self) -> "HourEntry":
        for field_name in ("demand_kwh", "solar_kwh", "tariff_bdt_per_kwh"):
            val = getattr(self, field_name)
            if not math.isfinite(val):
                raise ValueError(f"{field_name} must be a finite number, got {val}")
        return self


class BatteryConfig(BaseModel):
    """Battery capacity, starting state, reserve, and rate limits (Section 07.3)."""
    model_config = ConfigDict(extra="forbid")

    capacity_kwh: float = Field(..., gt=0)
    initial_energy_kwh: float = Field(..., ge=0)
    minimum_energy_kwh: float = Field(..., ge=0)
    max_charge_kwh_per_hour: float = Field(..., ge=0)
    max_discharge_kwh_per_hour: float = Field(..., ge=0)

    @model_validator(mode="after")
    def validate_battery_bounds(self) -> "BatteryConfig":
        # Finite check for all numeric fields
        for field_name in (
            "capacity_kwh", "initial_energy_kwh", "minimum_energy_kwh",
            "max_charge_kwh_per_hour", "max_discharge_kwh_per_hour",
        ):
            val = getattr(self, field_name)
            if not math.isfinite(val):
                raise ValueError(f"{field_name} must be a finite number, got {val}")

        # Relational bounds
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError("initial_energy_kwh cannot be below minimum_energy_kwh")
        return self


class OptimizeEnergyRequest(BaseModel):
    """POST /optimize-energy request body — Problem Statement Section 07."""
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(..., min_length=1)
    operator_notes: list[str] = Field(..., min_length=1, max_length=10)
    hours: list[HourEntry] = Field(..., min_length=24, max_length=24)
    battery: BatteryConfig

    @field_validator("operator_notes")
    @classmethod
    def notes_non_empty(cls, v: list[str]) -> list[str]:
        for i, note in enumerate(v):
            if not note.strip():
                raise ValueError(f"operator_notes[{i}] must be a non-empty string")
        return v

    @field_validator("hours")
    @classmethod
    def hours_complete_and_ordered(cls, v: list[HourEntry]) -> list[HourEntry]:
        seen = set()
        for entry in v:
            if entry.hour in seen:
                raise ValueError(f"Duplicate hour entry: {entry.hour}")
            seen.add(entry.hour)
        if seen != set(range(24)):
            raise ValueError("hours must contain exactly hours 0 through 23")
        return sorted(v, key=lambda h: h.hour)
