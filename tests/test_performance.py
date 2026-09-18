"""
Phase 8 — Performance Tests

Ensures that the API pipeline reliably meets latency targets.
Target: p95 latency < 5.0 seconds.
Hard fail: Any single request > 30.0 seconds.
"""

import time
import pytest
import statistics
from httpx import AsyncClient, ASGITransport

from app.main import app
from tests.conftest import make_valid_payload


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_performance_latency(client, valid_payload):
    """
    Measure p95 latency over N repeated calls.
    Note: In a pure local test without OpenAI key, it hits the offline fallback,
    so it tests purely the FastAPI + Validator + PuLP Optimizer pipeline.
    This should be blazingly fast (<< 1s).
    """
    ITERATIONS = 15
    latencies = []
    
    # Warm up (JIT, caches, etc.)
    await client.post("/optimize-energy", json=valid_payload)
    
    for _ in range(ITERATIONS):
        start = time.perf_counter()
        resp = await client.post("/optimize-energy", json=valid_payload)
        duration = time.perf_counter() - start
        
        assert resp.status_code == 200, f"Performance test failed with {resp.status_code}"
        assert duration < 28.0, f"Hard timeout threshold breached! Took {duration:.2f}s"
        latencies.append(duration)
        
    p95 = statistics.quantiles(latencies, n=100)[94]
    
    # The pure mathematical pipeline should easily clear a 5s budget.
    # LLM network latency adds ~1-3s in production, keeping us well under 5s total.
    assert p95 < 5.0, f"p95 latency {p95:.2f}s exceeded target 5.0s"
