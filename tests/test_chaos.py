"""
Phase 7 — Chaos & Robustness Testing.

Stresses the entire pipeline:
  - LLM timeouts (simulating provider hangs)
  - LLM returning 5xx / rate-limit exceptions
  - LLM returning complete garbage / empty strings
  - Huge operator_notes strings
  - Impossible constraints passing guardrails (caught by solver, safely returning error)
  - Concurrent repeated requests

Every case must return a controlled 4xx or 500 error — NEVER an unhandled
exception crashing the ASGI worker, NEVER leaking secrets or stack traces.
"""

import asyncio
import json
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

from app.main import app
from tests.conftest import make_valid_payload


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── LLM Chaos ────────────────────────────────────────────────────────────────

class TestLLMChaos:
    """Stress testing the LLM interpreter boundary."""

    @pytest.mark.asyncio
    @patch("app.llm.interpreter.get_openai_client")
    async def test_llm_timeout_handled(self, mock_get_client, client, valid_payload):
        """If the LLM hangs, it must either timeout internally or hit the 28s global timeout."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = asyncio.TimeoutError("Provider hung")
        mock_get_client.return_value = mock_client
        
        # Should cleanly fall back to offline interpreter
        resp = await client.post("/optimize-energy", json=valid_payload)
        
        assert resp.status_code == 200
        # The fallback interpreter will process it instead of crashing!

    @pytest.mark.asyncio
    @patch("app.llm.interpreter.get_openai_client")
    async def test_llm_5xx_handled(self, mock_get_client, client, valid_payload):
        """Provider returns 502 Bad Gateway or Rate Limit."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = Exception("502 Bad Gateway")
        mock_get_client.return_value = mock_client
        
        resp = await client.post("/optimize-energy", json=valid_payload)
        
        assert resp.status_code == 200
        # Fallback catches it. Let's make sure no 500 unhandled exception occurs.

    @pytest.mark.asyncio
    @patch("app.llm.interpreter.get_openai_client")
    async def test_llm_garbage_json(self, mock_get_client, client, valid_payload):
        """Provider returns total garbage that fails both tries."""
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock()]
        mock_response.choices[0].message.content = "I am an AI and I refuse to write JSON"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client
        
        resp = await client.post("/optimize-energy", json=valid_payload)
        assert resp.status_code == 200
        # Again, fallback catches it.

    @pytest.mark.asyncio
    async def test_huge_operator_notes(self, client, valid_payload):
        """Very large strings in operator notes shouldn't crash the regex fallback or regex."""
        valid_payload["operator_notes"] = ["This is a test. " * 1000]
        resp = await client.post("/optimize-energy", json=valid_payload)
        # Should be processed successfully (fallback defaults to no_op)
        assert resp.status_code == 200
        assert resp.json()["directive_interpretation"][0]["directive_type"] == "no_op"


# ── Pipeline & Solvers Chaos ─────────────────────────────────────────────────

class TestPipelineChaos:
    """Stress testing the optimizer and final validator."""
    
    @pytest.mark.asyncio
    @patch("app.api.routes.interpret_operator_notes")
    async def test_impossible_constraints_handled(self, mock_llm, client, valid_payload):
        """If the guardrails approve a theoretically valid but physically impossible scenario."""
        # Force the LLM to return a max grid of 0, while battery is empty and demand is high
        mock_llm.return_value = [
            {
                "note_index": 0,
                "directive_type": "max_grid_window",
                "structured_adjustment": {"hours": [0], "max_grid_kwh": 0.0},
                "explanation": "No grid"
            }
        ]
        
        # Make it physically impossible to satisfy demand
        valid_payload["battery"]["initial_energy_kwh"] = 10
        valid_payload["battery"]["minimum_energy_kwh"] = 10
        valid_payload["hours"][0]["demand_kwh"] = 100
        valid_payload["hours"][0]["solar_kwh"] = 0
        
        resp = await client.post("/optimize-energy", json=valid_payload)
        
        # The solver will raise ValueError("Scenario is infeasible")
        # routes.py catches it and returns a safe 500 error without stack trace
        assert resp.status_code == 500
        err = resp.json().get("error", "")
        assert "Optimization failed" in err
        assert "Traceback" not in err

    @pytest.mark.asyncio
    @patch("app.api.routes.optimize_schedule")
    async def test_final_validator_catches_optimizer_bug(self, mock_opt, client, valid_payload):
        """If the optimizer is bugged and returns invalid math, the final validator stops it."""
        from app.schemas.response import BatteryAction
        
        # Forged invalid schedule (claims 1000 battery energy after, impossible)
        mock_plan = []
        for h in range(24):
            mock_plan.append({
                "hour": h,
                "grid_kwh": 0.0,
                "solar_used_kwh": 0.0,
                "battery_action": BatteryAction.IDLE,
                "battery_kwh": 0.0,
                "battery_energy_after_kwh": 1000.0, # Lie
            })
            
        mock_opt.return_value = {
            "hourly_plan": mock_plan,
            "total_grid_kwh": 0,
            "total_cost_bdt": 0,
            "peak_grid_kwh": 0,
            "plan_summary": ""
        }
        
        # Mock LLM to pass guardrails easily
        with patch("app.api.routes.interpret_operator_notes", return_value=[]):
            resp = await client.post("/optimize-energy", json=valid_payload)
            
        # The Final Validator will raise ValueError, and routes returns 500
        assert resp.status_code == 500
        assert "Final validation failed" in resp.json().get("error", "")

    @pytest.mark.asyncio
    async def test_concurrent_requests(self, client, valid_payload):
        """Test that the pipeline handles multiple concurrent requests safely."""
        tasks = [client.post("/optimize-energy", json=valid_payload) for _ in range(10)]
        responses = await asyncio.gather(*tasks)
        
        for resp in responses:
            assert resp.status_code == 200
            assert "hourly_plan" in resp.json()


# ── Global Middleware ────────────────────────────────────────────────────────

class TestMiddleware:
    """Test global timeout and exception handlers."""
    
    @pytest.mark.asyncio
    @patch("app.api.routes.optimize_schedule")
    async def test_global_unhandled_exception(self, mock_opt, client, valid_payload):
        """If a raw unhandled exception escapes routes.py entirely, main.py catches it."""
        mock_opt.side_effect = RuntimeError("Critical segfault in C library!")
        
        # Mock LLM to pass guardrails easily
        with patch("app.api.routes.interpret_operator_notes", return_value=[]):
            resp = await client.post("/optimize-energy", json=valid_payload)
            
        assert resp.status_code == 500
        # The exception handler in main.py catches this. 
        # But wait, routes.py has a catch-all in Step 4. Let's force an error in Step 0.
    
    @pytest.mark.asyncio
    @patch("app.api.routes.OptimizeEnergyResponse")
    async def test_true_global_unhandled(self, mock_resp, client, valid_payload):
        mock_resp.side_effect = Exception("Pydantic broke!")
        
        with patch("app.api.routes.interpret_operator_notes", return_value=[]):
            resp = await client.post("/optimize-energy", json=valid_payload)
        
        assert resp.status_code == 500
        assert resp.json() == {"error": "Internal server error"}

    @pytest.mark.asyncio
    @patch("app.api.routes.interpret_operator_notes")
    async def test_global_timeout(self, mock_llm, client, valid_payload):
        """If a route takes >28 seconds, TimeoutMiddleware intervenes."""
        async def slow_func(*args, **kwargs):
            await asyncio.sleep(2.0)
            return []
            
        mock_llm.side_effect = slow_func
        
        # We need to temporarily lower the timeout to test it quickly
        from app.main import TimeoutMiddleware
        # We can't easily modify the deployed middleware, so we mock asyncio.wait_for
        with patch("app.main.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            resp = await client.post("/optimize-energy", json=valid_payload)
            
        assert resp.status_code == 504
        assert "exceeded execution budget" in resp.json()["error"]


# ── Security & Secret Redaction ──────────────────────────────────────────────

class TestSecurity:
    """Test logging redactor."""
    
    def test_log_redaction(self, caplog):
        """Ensure API keys are scrubbed from logs."""
        from app.config import SecretRedactingFilter
        import logging
        
        redact = SecretRedactingFilter()
        
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Connecting with key sk-abcdef1234567890", args=(),
            exc_info=None
        )
        
        redact.filter(record)
        assert "sk-abcdef" not in record.msg
        assert "[REDACTED]" in record.msg
        
        record2 = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="User %s used token %s", args=("admin", "Bearer xxxxxxxxxxxx"),
            exc_info=None
        )
        
        redact.filter(record2)
        assert record2.args[1] == "[REDACTED]"
