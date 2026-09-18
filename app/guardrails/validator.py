"""
Guardrail Validator — deterministic validation of LLM-produced directive
interpretations BEFORE they reach the optimizer.

Checks (Problem Statement Section 08):
  - directive_type is one of the 6 supported types, else demote to no_op
  - note_index maps exactly 1:1 to notes (0 to num_notes-1); fill missing, drop duplicates
  - hours are unique ints 0–23 in ascending order; if empty, demote to no_op
  - solar_reduction factor is finite and clamped to [0, 1]
  - battery reserve is finite, non-negative, and ≤ capacity
  - max_grid_kwh is finite and non-negative
  - applies semantics: no_op → false, everything else → true
  - no_op → structured_adjustment = null
  - no invention of unsupported fields or rewriting scenario data

This layer NEVER throws exceptions. Any malformed input is safely neutralized
to a harmless no_op.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.schemas.request import BatteryConfig
from app.schemas.response import DirectiveInterpretationEntry, DirectiveType

logger = logging.getLogger(__name__)


def _make_no_op(note_index: int, explanation: str) -> DirectiveInterpretationEntry:
    """Create a safe no_op entry for a given index."""
    return DirectiveInterpretationEntry(
        note_index=note_index,
        applies=False,
        directive_type=DirectiveType.NO_OP,
        structured_adjustment=None,
        explanation=explanation,
    )


def _safe_float(value: Any) -> float | None:
    """Extract a finite float, or None if invalid."""
    try:
        if value is None or isinstance(value, bool):
            return None
        val = float(value)
        if math.isfinite(val):
            return val
        return None
    except (ValueError, TypeError):
        return None


def validate_directives(
    raw_interpretations: list[dict[str, Any]],
    num_notes: int,
    battery: BatteryConfig,
) -> list[DirectiveInterpretationEntry]:
    """
    Validate raw LLM interpretations against all guardrail rules.
    Never throws exceptions; safely demotes malformed inputs to no_op.
    """
    # 1. Map raw interpretations by their note_index
    index_map: dict[int, dict[str, Any]] = {}
    
    # If the LLM returned a list of something else (e.g. strings), handle safely
    if isinstance(raw_interpretations, list):
        for raw in raw_interpretations:
            if isinstance(raw, dict):
                idx = raw.get("note_index")
                if isinstance(idx, int) and 0 <= idx < num_notes:
                    if idx not in index_map:
                        index_map[idx] = raw
    
    validated = []
    
    # 2. Process exactly one entry per note index
    for i in range(num_notes):
        raw = index_map.get(i)
        
        # Missing or invalid wrapper → safe no_op
        if not raw:
            validated.append(_make_no_op(i, "Note missing from LLM output (auto-corrected to no_op)."))
            continue
            
        original_explanation = str(raw.get("explanation", "No explanation provided."))
        
        # 3. Extract and validate directive_type
        raw_type = raw.get("directive_type")
        if not isinstance(raw_type, str):
            validated.append(_make_no_op(i, "Invalid directive_type format (auto-corrected to no_op)."))
            continue
            
        try:
            dtype = DirectiveType(raw_type)
        except ValueError:
            validated.append(_make_no_op(i, f"Unsupported directive_type '{raw_type}' (demoted to no_op)."))
            continue
            
        if dtype == DirectiveType.NO_OP:
            validated.append(_make_no_op(i, original_explanation))
            continue
            
        # 4. Extract structured_adjustment
        adj = raw.get("structured_adjustment")
        if not isinstance(adj, dict):
            validated.append(_make_no_op(i, "Missing or invalid structured_adjustment (demoted to no_op)."))
            continue
            
        # 5. Extract and validate hours
        raw_hours = adj.get("hours")
        if not isinstance(raw_hours, list):
            validated.append(_make_no_op(i, "Missing or invalid hours array (demoted to no_op)."))
            continue
            
        valid_hours = set()
        for h in raw_hours:
            if isinstance(h, (int, float)) and not isinstance(h, bool):
                try:
                    ih = int(h)
                    if 0 <= ih <= 23:
                        valid_hours.add(ih)
                except (ValueError, TypeError):
                    pass
                    
        if not valid_hours:
            validated.append(_make_no_op(i, "No valid hours in 0-23 range (demoted to no_op)."))
            continue
            
        sorted_hours = sorted(list(valid_hours))
        
        # 6. Specific shape validation per directive_type
        final_adj: dict[str, Any] = {"hours": sorted_hours}
        
        if dtype == DirectiveType.SOLAR_REDUCTION:
            factor = _safe_float(adj.get("factor"))
            if factor is None:
                validated.append(_make_no_op(i, "Invalid or missing solar factor (demoted to no_op)."))
                continue
            # Clamp to [0, 1]
            factor = max(0.0, min(1.0, factor))
            final_adj["factor"] = factor
            
        elif dtype == DirectiveType.MINIMUM_BATTERY_RESERVE:
            min_kwh = _safe_float(adj.get("minimum_energy_kwh"))
            if min_kwh is None or min_kwh < 0:
                validated.append(_make_no_op(i, "Invalid battery reserve value (demoted to no_op)."))
                continue
            # Cap at battery capacity
            min_kwh = min(min_kwh, battery.capacity_kwh)
            final_adj["minimum_energy_kwh"] = min_kwh
            
        elif dtype == DirectiveType.MAX_GRID_WINDOW:
            max_grid = _safe_float(adj.get("max_grid_kwh"))
            if max_grid is None or max_grid < 0:
                validated.append(_make_no_op(i, "Invalid max_grid_kwh value (demoted to no_op)."))
                continue
            final_adj["max_grid_kwh"] = max_grid
            
        elif dtype in (DirectiveType.NO_CHARGE_WINDOW, DirectiveType.NO_DISCHARGE_WINDOW):
            # No extra fields required
            pass
            
        # 7. Add the fully validated, strict-shaped entry
        validated.append(DirectiveInterpretationEntry(
            note_index=i,
            applies=True,
            directive_type=dtype,
            structured_adjustment=final_adj,
            explanation=original_explanation,
        ))

    return validated
