"""
GridWise LLM — FastAPI Application Entry Point

Pipeline: Energy Data + Operator Notes
            → LLM Interpreter
            → Guardrail Validator
            → Math Optimizer (LP)
            → Final Validator
            → API Response
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import setup_logging
from app.api.routes import router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle."""
    setup_logging()
    logger.info("GridWise LLM service starting up")
    yield
    logger.info("GridWise LLM service shutting down")


app = FastAPI(
    title="GridWise LLM",
    description="BUP CSE Fest 2026 — Smart Campus Energy Optimization with LLM-Assisted Operator Directive Interpretation",
    version="1.0.0",
    lifespan=lifespan,
)

import asyncio
from starlette.middleware.base import BaseHTTPMiddleware

class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), timeout=28.0)
        except asyncio.TimeoutError:
            logger.error("Request %s %s timed out after 28s", request.method, request.url.path)
            return JSONResponse(
                status_code=504,
                content={"error": "Request timed out (exceeded execution budget)"}
            )
        except Exception as exc:
            logger.exception("Unhandled exception trapped by middleware on %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error"}
            )

app.add_middleware(TimeoutMiddleware)
app.include_router(router)


# ── Global exception handlers — never leak secrets or raw tracebacks ──

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all: return a controlled 500 without exposing internals."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )
