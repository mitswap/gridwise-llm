"""
API Routes — GET /health and POST /optimize-energy.

Pipeline orchestration: LLM → Guardrails → Optimizer → Final Validator.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.schemas.request import OptimizeEnergyRequest
from app.schemas.response import OptimizeEnergyResponse
from app.llm.interpreter import interpret_operator_notes
from app.guardrails.validator import validate_directives
from app.optimizer.solver import optimize_schedule
from app.validator.replay import validate_final_plan

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximum request body size: 1 MB (protects against huge payloads)
MAX_BODY_BYTES = 1_048_576


# ── GET /health ──────────────────────────────────────────────────────────────

@router.get("/health")
async def health() -> dict:
    """Readiness endpoint — must return {"status": "ok"} (Section 06.2)."""
    return {"status": "ok"}


# ── POST /optimize-energy ────────────────────────────────────────────────────

@router.post(
    "/optimize-energy",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": {
                        "scenario_id": "SAMPLE-01",
                        "hours": [
                            {"hour": 0, "demand_kwh": 100, "solar_kwh": 0, "tariff_bdt_per_kwh": 8},
                            {"hour": 1, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 8},
                            {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 8}
                        ],
                        "battery": {
                            "capacity_kwh": 200,
                            "initial_energy_kwh": 50,
                            "minimum_energy_kwh": 20,
                            "max_charge_kwh_per_hour": 50,
                            "max_discharge_kwh_per_hour": 50
                        },
                        "operator_notes": ["Reserve 30 kWh from 9:00 to 11:00."]
                    }
                }
            },
            "required": True,
        }
    }
)
async def optimize_energy(request: Request) -> JSONResponse:
    """
    Main endpoint — full pipeline:
      1. Parse & validate request (Pydantic → 400 on malformed)
      2. LLM Interpreter   → raw directive interpretations
      3. Guardrail Validator → validated, safe directives
      4. Math Optimizer     → 24-hour schedule (LP)
      5. Final Validator    → replay & verify all constraints
      6. Return response
    """
    # ── Step 0: Read body, enforce size limit, parse JSON ────────────────
    try:
        body_bytes = await request.body()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Failed to read request body"})

    if len(body_bytes) > MAX_BODY_BYTES:
        logger.warning("Request body too large: %d bytes", len(body_bytes))
        return JSONResponse(
            status_code=400,
            content={"error": f"Request body too large (max {MAX_BODY_BYTES} bytes)"},
        )

    if not body_bytes:
        return JSONResponse(status_code=400, content={"error": "Empty request body"})

    try:
        body = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Malformed JSON body received")
        return JSONResponse(status_code=400, content={"error": "Malformed JSON"})

    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"error": "Request body must be a JSON object"},
        )

    # ── Step 1: Validate against Pydantic schema ────────────────────────
    try:
        req = OptimizeEnergyRequest(**body)
    except ValidationError as exc:
        # Format Pydantic errors cleanly — field path + message, no secrets
        errors = []
        for err in exc.errors():
            loc = " → ".join(str(x) for x in err.get("loc", []))
            msg = err.get("msg", "validation error")
            errors.append(f"{loc}: {msg}" if loc else msg)
        detail = "; ".join(errors[:10])  # cap at 10 errors
        logger.warning("Request validation failed: %s", detail[:200])
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid request", "detail": detail[:500]},
        )
    except Exception as exc:
        logger.warning("Unexpected validation error: %s", str(exc)[:200])
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid request", "detail": str(exc)[:500]},
        )

    # ── Step 2: LLM Interpretation ───────────────────────────────────────
    try:
        raw_interpretations = await interpret_operator_notes(
            operator_notes=req.operator_notes,
            hours=req.hours,
            battery=req.battery,
        )
    except Exception as exc:
        logger.exception("LLM interpretation failed")
        return JSONResponse(
            status_code=500,
            content={"error": "LLM interpretation failed"},
        )

    # ── Step 3: Guardrail Validation ─────────────────────────────────────
    try:
        validated_directives = validate_directives(
            raw_interpretations=raw_interpretations,
            num_notes=len(req.operator_notes),
            battery=req.battery,
        )
    except Exception as exc:
        logger.exception("Guardrail validation failed")
        return JSONResponse(
            status_code=500,
            content={"error": "Directive validation failed"},
        )

    # ── Step 4: Optimization ─────────────────────────────────────────────
    try:
        schedule = optimize_schedule(
            hours=req.hours,
            battery=req.battery,
            directives=validated_directives,
        )
    except Exception as exc:
        logger.exception("Optimization failed")
        return JSONResponse(
            status_code=500,
            content={"error": "Optimization failed"},
        )

    # ── Step 5: Final Validation ─────────────────────────────────────────
    try:
        validated_plan = validate_final_plan(
            schedule=schedule,
            hours=req.hours,
            battery=req.battery,
            directives=validated_directives,
        )
    except Exception as exc:
        logger.exception("Final validation failed")
        return JSONResponse(
            status_code=500,
            content={"error": "Final validation failed"},
        )

    # ── Step 6: Build response ───────────────────────────────────────────
    response = OptimizeEnergyResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=validated_directives,
        hourly_plan=validated_plan["hourly_plan"],
        total_grid_kwh=validated_plan["total_grid_kwh"],
        total_cost_bdt=validated_plan["total_cost_bdt"],
        peak_grid_kwh=validated_plan["peak_grid_kwh"],
        plan_summary=validated_plan["plan_summary"],
    )

    return JSONResponse(status_code=200, content=response.model_dump())
