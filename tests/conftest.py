"""
Shared test fixtures for GridWise LLM tests.
"""

import copy
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.fixture
def async_client():
    """Create an HTTPX async client wired to the FastAPI app."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def make_valid_payload() -> dict:
    """
    Build a complete, valid POST /optimize-energy request payload.
    Based on the Problem Statement example (Section 07.4).
    """
    # Realistic 24-hour campus scenario
    solar_profile = [
        0, 0, 0, 0, 0, 0,       # hours 0-5:  nighttime
        10, 30, 60, 90, 110, 120,  # hours 6-11: morning ramp
        130, 120, 100, 80, 50, 20, # hours 12-17: afternoon
        0, 0, 0, 0, 0, 0,         # hours 18-23: evening
    ]
    demand_profile = [
        180, 160, 150, 140, 140, 150,  # hours 0-5
        200, 250, 300, 320, 310, 290,  # hours 6-11
        280, 300, 310, 290, 270, 260,  # hours 12-17
        280, 300, 280, 250, 220, 200,  # hours 18-23
    ]
    tariff_profile = [
        7, 7, 7, 7, 7, 7,       # hours 0-5:  off-peak
        8, 9, 10, 11, 11, 10,   # hours 6-11: rising
        9, 9, 10, 11, 12, 12,   # hours 12-17: peak
        11, 10, 9, 8, 7, 7,     # hours 18-23: declining
    ]

    return {
        "scenario_id": "GRID-101",
        "operator_notes": [
            "Solar output will drop to about 20% from 1 PM to 3 PM.",
            "Do not charge the battery between 2 PM and 4 PM.",
            "The cafeteria menu changes tomorrow.",
        ],
        "hours": [
            {
                "hour": h,
                "demand_kwh": demand_profile[h],
                "solar_kwh": solar_profile[h],
                "tariff_bdt_per_kwh": tariff_profile[h],
            }
            for h in range(24)
        ],
        "battery": {
            "capacity_kwh": 500,
            "initial_energy_kwh": 200,
            "minimum_energy_kwh": 50,
            "max_charge_kwh_per_hour": 100,
            "max_discharge_kwh_per_hour": 100,
        },
    }


@pytest.fixture
def valid_payload() -> dict:
    """Return a deep copy of the valid payload for mutation in tests."""
    return make_valid_payload()
