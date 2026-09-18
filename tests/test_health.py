"""
Test: GET /health returns 200 with {"status": "ok"}.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_health_returns_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_method_not_allowed_post():
    """POST to /health should be 405."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/health")
    assert resp.status_code == 405
