"""
Application configuration — all settings from environment variables.
Never hard-code secrets. See .env.example for required variables.
"""

from __future__ import annotations

import os
import logging
import re
from dotenv import load_dotenv

load_dotenv()  # loads .env if present (never committed)


# ── LLM Provider ──
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL: str | None = os.getenv("OPENAI_BASE_URL")
LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "15"))

# ── Server ──
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info").upper()


# ── Logging with secret redaction ──

# Patterns that look like API keys / tokens
_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_-]{10,})"),           # OpenAI-style keys
    re.compile(r"(Bearer\s+[A-Za-z0-9_\-\.]{10,})"),   # Bearer tokens
    re.compile(r"(api[_-]?key\s*[:=]\s*\S+)", re.I),   # key=value leaks
    re.compile(r"(token\s*[:=]\s*\S+)", re.I),          # token=value leaks
]


class SecretRedactingFilter(logging.Filter):
    """Logging filter that replaces secret-like patterns with [REDACTED]."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pat in _SECRET_PATTERNS:
                record.msg = pat.sub("[REDACTED]", record.msg)
        if record.args:
            new_args = []
            for arg in (record.args if isinstance(record.args, tuple) else (record.args,)):
                if isinstance(arg, str):
                    for pat in _SECRET_PATTERNS:
                        arg = pat.sub("[REDACTED]", arg)
                new_args.append(arg)
            record.args = tuple(new_args)
        return True


def setup_logging() -> None:
    """Configure structured logging with secret redaction on all handlers."""
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    logging.basicConfig(level=LOG_LEVEL, format=log_format)

    # Attach redacting filter to root logger so it covers every handler
    root = logging.getLogger()
    redact = SecretRedactingFilter()
    for handler in root.handlers:
        handler.addFilter(redact)
