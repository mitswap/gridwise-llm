"""
Phase 8 — Property-Based Invariant Testing (Hypothesis)

Generates random valid scenarios and asserts every physical and business
invariant in CONTRACT.md holds on the output plan, regardless of the input data.
"""

from hypothesis import given, settings, strategies as st
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.schemas.request import OptimizeEnergyRequest, HourEntry, BatteryConfig


# ── Strategies ───────────────────────────────────────────────────────────────

@st.composite
def battery_strategy(draw):
    capacity = draw(st.floats(min_value=10.0, max_value=1000.0, allow_nan=False, allow_infinity=False))
    minimum = draw(st.floats(min_value=0.0, max_value=capacity, allow_nan=False, allow_infinity=False))
    initial = draw(st.floats(min_value=minimum, max_value=capacity, allow_nan=False, allow_infinity=False))
    max_charge = draw(st.floats(min_value=0.0, max_value=capacity, allow_nan=False, allow_infinity=False))
    max_discharge = draw(st.floats(min_value=0.0, max_value=capacity, allow_nan=False, allow_infinity=False))
    
    return BatteryConfig(
        capacity_kwh=capacity,
        initial_energy_kwh=initial,
        minimum_energy_kwh=minimum,
        max_charge_kwh_per_hour=max_charge,
        max_discharge_kwh_per_hour=max_discharge
    )

@st.composite
def hours_strategy(draw):
    hours = []
    for h in range(24):
        demand = draw(st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False))
        solar = draw(st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False))
        tariff = draw(st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False))
        
        hours.append(HourEntry(
            hour=h,
            demand_kwh=demand,
            solar_kwh=solar,
            tariff_bdt_per_kwh=tariff
        ))
    return hours

@st.composite
def scenario_strategy(draw):
    battery = draw(battery_strategy())
    hours = draw(hours_strategy())
    # Property tests will primarily test the math solver using no_op LLM fallback
    # to avoid LLM rate limits during 100+ fuzzed iterations.
    notes = ["Property test"]
    
    return OptimizeEnergyRequest(
        scenario_id="fuzz",
        operator_notes=notes,
        hours=hours,
        battery=battery
    )


# ── Property Tests ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"

# Since Hypothesis doesn't mix easily with FastAPI's TestClient inside async defs natively
# without complex event loop management, we use the optimizer route directly for deep math fuzzing.
from app.optimizer.solver import optimize_schedule
from app.validator.replay import validate_final_plan
from app.guardrails.validator import validate_directives
from app.llm.fallback import interpret_notes_offline

@given(scenario_strategy())
@settings(max_examples=50, deadline=1000)
def test_all_physical_invariants_hold(scenario: OptimizeEnergyRequest):
    """
    Fuzz the optimizer with random valid battery/hour inputs.
    Assert that the Replay Validator passes cleanly.
    The Replay Validator inherently enforces:
    - Energy balance
    - Battery bounds (0 <= min <= current <= cap)
    - Rate limits
    - End-of-day neutrality
    - No negative numbers
    """
    # 1. Mock interpretation (offline fallback)
    raw = interpret_notes_offline(scenario.operator_notes)
    
    # 2. Guardrails
    directives = validate_directives(raw, len(scenario.operator_notes), scenario.battery)
    
    # 3. Optimize
    # Note: PuLP may legitimately raise ValueError if physically impossible
    # but since there are no active constraints limiting grid import in this baseline fuzzer,
    # it should always be feasible (grid can grow to infinity).
    schedule = optimize_schedule(scenario.hours, scenario.battery, directives)
    
    # 4. Final Replay Validator MUST PASS!
    # If the solver produces mathematical drift, the replay validator will throw ValueError.
    validate_final_plan(schedule, scenario.hours, scenario.battery, directives)
