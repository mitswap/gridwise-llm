"""
Rule-based offline fallback interpreter.

Used ONLY when the LLM is unavailable (no API key, quota exhausted,
provider down). This is NOT the primary interpreter — the contract
requires a real language model for paraphrase-robust interpretation.

This fallback uses keyword matching + regex time extraction to provide
a best-effort classification so the service does not crash.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Word-to-number mapping for text-based times ─────────────────────────────

_WORD_NUMS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "noon": 12, "midnight": 0,
}


def _to_24h(hour: int, ampm: str | None) -> int:
    """Convert 12-hour time to 24-hour."""
    if ampm is None:
        return hour
    ampm = ampm.strip().lower()
    if ampm == "am":
        return hour % 12
    elif ampm == "pm":
        return (hour % 12) + 12
    return hour


def _extract_hours(text: str) -> list[int]:
    """Extract a whole-hour time window from natural language (start-inclusive, end-exclusive)."""
    t = text.lower()

    # Replace word numbers with digits
    for word, num in _WORD_NUMS.items():
        t = re.sub(rf"\b{word}\b", str(num), t)

    # Pattern: "X PM to/until/through/- Y PM" or "X AM to Y AM"
    m = re.search(
        r"(\d{1,2})\s*(am|pm)?\s*(?:to|until|through|[-–])\s*(\d{1,2})\s*(am|pm)?",
        t, re.I,
    )
    if m:
        start = _to_24h(int(m.group(1)), m.group(2) or m.group(4))
        end = _to_24h(int(m.group(3)), m.group(4))
        if 0 <= start < end <= 24:
            return list(range(start, end))

    # Pattern: "between X and Y PM/AM/optional"
    m = re.search(
        r"between\s+(\d{1,2})\s*(am|pm)?\s*and\s+(\d{1,2})\s*(am|pm)?",
        t, re.I,
    )
    if m:
        start = _to_24h(int(m.group(1)), m.group(2) or m.group(4))
        end = _to_24h(int(m.group(3)), m.group(4))
        if 0 <= start < end <= 24:
            return list(range(start, end))

    # Pattern: "HH:00 to HH:00" or "between HH:00 and HH:00"
    m = re.search(r"(?:between\s+)?(\d{1,2}):00\s*(?:to|until|through|and|[-–])\s*(\d{1,2}):00", t)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        if 0 <= start < end <= 24:
            return list(range(start, end))

    # Pattern: "from X to/until Y PM" with implicit AM/PM
    m = re.search(
        r"from\s+(\d{1,2})\s*(am|pm)?\s*(?:to|until)\s+(\d{1,2})\s*(am|pm)?",
        t, re.I,
    )
    if m:
        start = _to_24h(int(m.group(1)), m.group(2) or m.group(4))
        end = _to_24h(int(m.group(3)), m.group(4))
        if 0 <= start < end <= 24:
            return list(range(start, end))

    return []


def _extract_number(text: str) -> float | None:
    """Extract the first meaningful number from text (kWh value, percentage, etc.)."""
    # Look for "X kWh" or "X kwh"
    m = re.search(r"(\d+(?:\.\d+)?)\s*kwh", text, re.I)
    if m:
        return float(m.group(1))
    # Look for standalone numbers (not hours)
    nums = re.findall(r"(\d+(?:\.\d+)?)", text)
    # Filter out likely hour values (small numbers near time words)
    for n in nums:
        val = float(n)
        if val > 24:  # likely a kWh value, not an hour
            return val
    return None


def _extract_factor(text: str) -> float:
    """Extract the solar factor (fraction remaining) from text."""
    t = text.lower()

    # "drops to X%", "only X%", "reduced to X%", "X% of normal", "about X%"
    m = re.search(r"(?:to|about|roughly|approximately|only)\s+(\d+(?:\.\d+)?)\s*%", t)
    if m:
        return float(m.group(1)) / 100.0

    # "X% reduction" → remaining = 1 - X/100
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:reduction|decrease|drop|cut|loss)", t)
    if m:
        return 1.0 - float(m.group(1)) / 100.0

    # "reduced by X%" → remaining = 1 - X/100
    m = re.search(r"(?:reduced?|cut|decreased?|drops?)\s+by\s+(\d+(?:\.\d+)?)\s*%", t)
    if m:
        return 1.0 - float(m.group(1)) / 100.0

    # "one-fifth", "one fifth" → 0.2
    if "one-fifth" in t or "one fifth" in t:
        return 0.2
    if "one-quarter" in t or "one quarter" in t:
        return 0.25
    if "half" in t:
        return 0.5
    if "one-third" in t or "one third" in t:
        return 1.0 / 3.0

    # Default: assume 50% if we can't parse
    return 0.5


def _make_no_op(note_index: int, explanation: str = "Not relevant to energy schedule.") -> dict:
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": explanation,
    }


def interpret_single_note_offline(note: str, note_index: int, battery_capacity: float | None = None) -> dict[str, Any]:
    """Interpret a single operator note using keyword matching."""
    t = note.lower()
    hours = _extract_hours(t)

    # ── solar_reduction ──
    solar_kws = ["solar", "pv", "panel", "photovoltaic", "rooftop"]
    reduce_kws = ["reduce", "drop", "cut", "decrease", "fall", "decline", "down to",
                   "only", "leave", "remain", "washing", "cleaning", "maintenance",
                   "shade", "cloud", "obstruct", "reduction"]
    if any(kw in t for kw in solar_kws) and any(kw in t for kw in reduce_kws):
        factor = _extract_factor(t)
        if hours:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": hours, "factor": round(factor, 4)},
                "explanation": f"Solar reduced to {factor*100:.0f}% during specified hours.",
            }

    # ── no_charge_window ── (check BEFORE no_discharge to avoid false match)
    charge_kws = ["charge", "charging", "charger"]
    block_kws = ["no ", "not ", "don't", "do not", "halt", "stop", "suspend",
                  "prevent", "disable", "prohibit", "avoid", "isolated", "unavailable", "disabled"]
    if any(kw in t for kw in charge_kws) and any(kw in t for kw in block_kws):
        # Make sure it's about charging, not discharging
        if "discharge" not in t and "discharging" not in t:
            if hours:
                return {
                    "note_index": note_index,
                    "applies": True,
                    "directive_type": "no_charge_window",
                    "structured_adjustment": {"hours": hours},
                    "explanation": "Battery charging blocked during specified hours.",
                }

    # ── no_discharge_window ──
    discharge_kws = ["discharge", "discharging", "drawn from"]
    if any(kw in t for kw in discharge_kws) and any(kw in t for kw in block_kws):
        if hours:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "Battery discharging blocked during specified hours.",
            }

    # ── minimum_battery_reserve ──
    reserve_kws = ["reserve", "minimum", "at least", "not below", "not fall below",
                    "maintain", "keep", "ensure", "must have", "backup", "stored in the battery"]
    if any(kw in t for kw in reserve_kws) and ("battery" in t or "kwh" in t or "storage" in t):
        value = _extract_number(t)
        if value is not None and hours:
            # Check for percentage of capacity
            if "%" in t and "capacity" in t and battery_capacity is not None:
                value = (value / 100.0) * battery_capacity
                
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {"hours": hours, "minimum_energy_kwh": value},
                "explanation": f"Battery reserve minimum set to {value} kWh.",
            }

    # ── max_grid_window ──
    grid_kws = ["grid", "transformer", "substation"]
    cap_kws = ["limit", "cap", "max", "not exceed", "at most", "ceiling",
               "restrict", "upper bound", "at or below"]
    if any(kw in t for kw in grid_kws) and any(kw in t for kw in cap_kws):
        value = _extract_number(t)
        if value is not None and hours:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "max_grid_window",
                "structured_adjustment": {"hours": hours, "max_grid_kwh": value},
                "explanation": f"Grid import capped at {value} kWh per hour.",
            }

    # ── Default: no_op ──
    return _make_no_op(note_index, "Note does not match any supported energy directive.")


def interpret_notes_offline(operator_notes: list[str], battery_capacity: float | None = None) -> list[dict[str, Any]]:
    """
    Offline fallback: interpret all notes using rule-based keyword matching.

    This is used ONLY when the LLM is unavailable. It will not handle
    paraphrases as well as a real language model.
    """
    logger.warning("Using OFFLINE rule-based fallback interpreter (LLM unavailable)")
    return [
        interpret_single_note_offline(note, i, battery_capacity)
        for i, note in enumerate(operator_notes)
    ]
