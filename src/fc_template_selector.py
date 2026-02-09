"""
FC Template Selector

Determines which of the 7 FC form template scenarios applies based on
SageMaker async inference output.

Template Scenarios:
  1. Ward + OR: ward_breakdown non-empty AND or_type exists with charges
  2. Ward only (days): ward exists, no OR, quantity_unit="days"
  3. Ward only (hours, 1 block): ward exists, no OR, quantity_unit="hours", 1 ward entry
  4. Ward only (hours, 2 blocks): ward exists, no OR, quantity_unit="hours", 2+ ward entries
  5. Ward only (2 types): 2+ ward entries with different ward_types, no OR
  6. OR only (1 block): no ward, OR exists, 1 charging block
  7. OR only (2 blocks): no ward, OR exists, 2+ charging blocks

Classification precedence (ward-only branch):
  - Two distinct ward_types -> template 5  (checked first)
  - quantity_unit="hours" with 2+ entries -> template 4
  - quantity_unit="hours" with 1 entry   -> template 3
  - Everything else (including "days")    -> template 2  (default)
"""


def _normalize_str(val) -> str | None:
    """Normalize a string value: strip whitespace, treat empty as None."""
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip()
        return val if val else None
    return str(val)


def _safe_numeric(val, default=0) -> float:
    """Safely convert a value to a number, returning default if invalid."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _has_ward(output: dict) -> bool:
    """Check if ward data exists with valid entries."""
    ward_breakdown = output.get("ward_breakdown")
    if not ward_breakdown:
        return False
    return any(
        _normalize_str(entry.get("ward_type")) is not None
        for entry in ward_breakdown
    )


def _has_or(output: dict) -> bool:
    """Check if OR (operating room) data exists with charges.

    Rule: (or_type is not None) AND ((or_charges > 0) OR (or_unit_cost_first_block > 0))
    """
    or_type = _normalize_str(output.get("or_type"))
    if or_type is None:
        return False
    or_charges = _safe_numeric(output.get("or_charges"))
    or_unit_cost = _safe_numeric(output.get("or_unit_cost_first_block"))
    return (or_charges > 0) or (or_unit_cost > 0)


def _get_ward_unit(output: dict) -> str | None:
    """Get the ward quantity unit (days/hours) from ward_breakdown."""
    ward_breakdown = output.get("ward_breakdown", [])
    if not ward_breakdown:
        return None
    first = ward_breakdown[0]
    unit = _normalize_str(first.get("ward_quantity_unit"))
    return unit.lower() if unit else None


def _get_distinct_ward_types(output: dict) -> list[str]:
    """Get distinct ward types from ward_breakdown."""
    ward_breakdown = output.get("ward_breakdown", [])
    seen = []
    for entry in ward_breakdown:
        wt = _normalize_str(entry.get("ward_type"))
        if wt and wt not in seen:
            seen.append(wt)
    return seen


def _get_or_block_count(output: dict) -> int:
    """Determine number of OR charging blocks.

    2 blocks if there's a subsequent-hour unit cost > 0, else 1.
    """
    or_unit_cost_subq = _safe_numeric(output.get("or_unit_cost_subq"))
    return 2 if or_unit_cost_subq > 0 else 1


def determine_template(output: dict) -> dict:
    """Determine which FC template scenario applies.

    Args:
        output: Parsed SageMaker async inference JSON output.

    Returns:
        dict with keys:
            template_id (int): 1-7 (0 for unclassified)
            template_name (str): Human-readable scenario name
            has_ward (bool)
            has_or (bool)
            ward_count (int): Number of ward_breakdown entries
            ward_unit (str | None): "days" or "hours" or None
    """
    has_ward = _has_ward(output)
    has_or = _has_or(output)
    ward_breakdown = output.get("ward_breakdown", []) or []
    ward_count = len(ward_breakdown)
    ward_unit = _get_ward_unit(output)

    if has_ward and has_or:
        template_id = 1
        template_name = "Ward + OR"
    elif has_ward and not has_or:
        distinct_types = _get_distinct_ward_types(output)
        # Precedence: 2-types (5) > hours-2-blocks (4) > hours-1-block (3) > days (2)
        if len(distinct_types) >= 2:
            template_id = 5
            template_name = "Ward only (2 types)"
        elif ward_unit == "hours":
            if ward_count >= 2:
                template_id = 4
                template_name = "Ward only (hours, 2 blocks)"
            else:
                template_id = 3
                template_name = "Ward only (hours, 1 block)"
        else:
            template_id = 2
            template_name = "Ward only (days)"
    elif not has_ward and has_or:
        or_blocks = _get_or_block_count(output)
        if or_blocks >= 2:
            template_id = 7
            template_name = "OR only (2 blocks)"
        else:
            template_id = 6
            template_name = "OR only (1 block)"
    else:
        template_id = 0
        template_name = "UNCLASSIFIED"

    return {
        "template_id": template_id,
        "template_name": template_name,
        "has_ward": has_ward,
        "has_or": has_or,
        "ward_count": ward_count,
        "ward_unit": ward_unit,
    }
