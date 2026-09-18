"""
Test: POST /optimize-energy returns 400 on malformed/invalid input.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_optimize_energy_malformed_json():
    """Non-JSON body should return 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/optimize-energy",
            content="this is not json",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_optimize_energy_missing_fields():
    """Empty JSON object should return 400 due to missing required fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/optimize-energy", json={})
    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_optimize_energy_empty_operator_notes():
    """Empty operator_notes array should return 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/optimize-energy", json={
            "scenario_id": "TEST-001",
            "operator_notes": [],
            "hours": [],
            "battery": {
                "capacity_kwh": 500,
                "initial_energy_kwh": 200,
                "minimum_energy_kwh": 50,
                "max_charge_kwh_per_hour": 100,
                "max_discharge_kwh_per_hour": 100,
            },
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_optimize_energy_wrong_hour_count():
    """Less than 24 hour entries should return 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/optimize-energy", json={
            "scenario_id": "TEST-002",
            "operator_notes": ["Some note"],
            "hours": [
                {"hour": 0, "demand_kwh": 100, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
            ],
            "battery": {
                "capacity_kwh": 500,
                "initial_energy_kwh": 200,
                "minimum_energy_kwh": 50,
                "max_charge_kwh_per_hour": 100,
                "max_discharge_kwh_per_hour": 100,
            },
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_optimize_energy_too_many_notes():
    """More than 3 operator notes should return 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/optimize-energy", json={
            "scenario_id": "TEST-003",
            "operator_notes": ["Note"] * 11,
            "hours": [
                {"hour": h, "demand_kwh": 100, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
                for h in range(24)
            ],
            "battery": {
                "capacity_kwh": 500,
                "initial_energy_kwh": 200,
                "minimum_energy_kwh": 50,
                "max_charge_kwh_per_hour": 100,
                "max_discharge_kwh_per_hour": 100,
            },
        })
    assert resp.status_code == 400
