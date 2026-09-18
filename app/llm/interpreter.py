"""
LLM Interpreter — converts natural-language operator notes into
structured directive interpretations using a language model.

Pipeline:
  1. Build prompt with all operator notes
  2. Call LLM (OpenAI-compatible API, configurable provider/model)
  3. Parse JSON response
  4. If parsing fails → retry with repair prompt (once)
  5. If retry fails → fall back to offline rule-based interpreter
  6. Return raw dicts (UNTRUSTED — must pass guardrail validation)

The LLM output is NEVER sent directly to the optimizer.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI

from app.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL, LLM_TIMEOUT
from app.schemas.request import HourEntry, BatteryConfig
from app.llm.prompts import SYSTEM_PROMPT, build_user_prompt, build_repair_prompt
from app.llm.fallback import interpret_notes_offline

logger = logging.getLogger(__name__)

# ── Lazy singleton client ────────────────────────────────────────────────────

_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    """Get or create the OpenAI-compatible async client (singleton)."""
    global _client
    if _client is None:
        kwargs: dict[str, Any] = {
            "api_key": OPENAI_API_KEY,
            "timeout": float(LLM_TIMEOUT),
        }
        if OPENAI_BASE_URL:
            kwargs["base_url"] = OPENAI_BASE_URL
        _client = AsyncOpenAI(**kwargs)
    return _client


def reset_client() -> None:
    """Reset the singleton client (useful for testing)."""
    global _client
    _client = None


# ── Public entry point ───────────────────────────────────────────────────────

async def interpret_operator_notes(
    operator_notes: list[str],
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> list[dict[str, Any]]:
    """
    Call the LLM to interpret each operator note into a structured directive.

    Returns a list of raw interpretation dicts (one per note, in order).
    The output is UNTRUSTED and must be validated by the guardrail layer.

    Falls back to offline rule-based interpreter if:
      - No API key is configured
      - LLM call fails (timeout, quota, provider error)
      - JSON parsing fails after retry

    Args:
        operator_notes: 1–3 natural-language notes from the operator.
        hours: The 24 hourly entries (context for the LLM).
        battery: Battery configuration (context).

    Returns:
        List of raw interpretation dicts with keys:
            note_index, applies, directive_type, structured_adjustment, explanation
    """
    if not OPENAI_API_KEY:
        logger.warning("No OPENAI_API_KEY configured — using offline fallback")
        return interpret_notes_offline(operator_notes, battery.capacity_kwh)

    try:
        raw = await _call_llm_with_retry(operator_notes)
        logger.info(
            "LLM interpreted %d note(s): types=%s",
            len(raw),
            [r.get("directive_type", "?") for r in raw],
        )
        return raw
    except Exception as exc:
        logger.error(
            "LLM interpretation failed after retry: %s — using offline fallback",
            str(exc)[:200],
        )
        return interpret_notes_offline(operator_notes, battery.capacity_kwh)


# ── LLM call with retry-on-parse-failure ─────────────────────────────────────

async def _call_llm_with_retry(
    operator_notes: list[str],
) -> list[dict[str, Any]]:
    """
    Call the LLM, parse JSON, retry once with repair prompt if parsing fails.

    Raises on final failure (caller falls back to offline interpreter).
    """
    client = get_openai_client()
    user_prompt = build_user_prompt(operator_notes)
    num_notes = len(operator_notes)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # ── Attempt 1 ────────────────────────────────────────────────────────
    content = await _call_llm(client, messages)
    try:
        return _parse_llm_response(content, num_notes)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as parse_err:
        logger.warning(
            "LLM response parse failed (attempt 1): %s",
            str(parse_err)[:150],
        )
        first_error = str(parse_err)

    # ── Attempt 2: Repair ────────────────────────────────────────────────
    logger.info("Retrying LLM with repair prompt")
    messages.append({"role": "assistant", "content": content or ""})
    messages.append({
        "role": "user",
        "content": build_repair_prompt(first_error, num_notes),
    })

    content = await _call_llm(client, messages)
    # This time, let exceptions propagate to the caller for fallback
    return _parse_llm_response(content, num_notes)


async def _call_llm(
    client: AsyncOpenAI,
    messages: list[dict[str, str]],
) -> str:
    """
    Make a single LLM API call. Tries with response_format=json first,
    falls back to plain text if provider doesn't support it.

    Returns the raw response content string.
    """
    model = OPENAI_MODEL

    # Try with JSON response format (supported by OpenAI, Groq, etc.)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if content:
            return content.strip()
    except Exception as e:
        err_str = str(e).lower()
        # If the error is about response_format, retry without it
        if "response_format" in err_str or "json" in err_str or "not supported" in err_str:
            logger.info("Provider doesn't support response_format=json, retrying without it")
        else:
            raise

    # Fallback: plain text (prompt already asks for JSON)
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.0,
    )
    content = response.choices[0].message.content
    return (content or "").strip()


# ── JSON parsing ─────────────────────────────────────────────────────────────

def _parse_llm_response(
    content: str,
    num_notes: int,
) -> list[dict[str, Any]]:
    """
    Parse the LLM response into a list of interpretation dicts.

    Handles:
      - Direct JSON array: [...]
      - JSON object with wrapper key: {"interpretations": [...]}
      - Markdown-fenced JSON: ```json ... ```

    Raises json.JSONDecodeError or ValueError on failure.
    """
    if not content:
        raise ValueError("Empty LLM response")

    # Strip markdown fences if present
    cleaned = _strip_markdown_fences(content)

    # Parse JSON
    data = json.loads(cleaned)

    # Handle wrapped object: {"interpretations": [...]} or similar
    if isinstance(data, dict):
        # Try common wrapper keys
        for key in ("interpretations", "directives", "results", "response", "data"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            # Single interpretation dict
            if "note_index" in data:
                data = [data]
            else:
                raise ValueError(
                    f"JSON object has no recognizable array key. Keys: {list(data.keys())}"
                )

    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")

    if len(data) != num_notes:
        logger.warning(
            "LLM returned %d interpretations, expected %d — will use what we got",
            len(data), num_notes,
        )

    # Basic sanity: each entry should be a dict
    result = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Interpretation [{i}] is not a dict: {type(item).__name__}")
        # Ensure note_index is present (set it if missing)
        if "note_index" not in item:
            item["note_index"] = i
        result.append(item)

    return result


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) from LLM output."""
    # Match ```json\n...\n``` or ```\n...\n```
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()
