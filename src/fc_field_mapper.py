"""
FC Field Mapper — Render-Ready Output

Maps raw SageMaker async inference output to a render-ready FC form structure.
The frontend can directly render the report without any processing logic.

Output structure:
- doctors_fees: rows with pre-formatted amounts, total, MOH benchmark
- hospital_charges: accommodation_rows, dtf_rows, ancillary, companion rate, total
- totals: all summary totals as formatted strings

All monetary values are formatted strings with commas and 2 decimal places
(e.g., "5,952.28"). Rate descriptions are pre-built strings matching the
FC form PDF layout (e.g., "$ 1,488.07 x 4 Day(s)").
"""

from datetime import datetime, timezone

DTF_FLAT_LABEL = "TREATMENT FEE-DAY SUITE"


def _safe_float(val, default=0.0) -> float:
    """Convert a value to float, returning default if None or invalid."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _money(val) -> float:
    """Round a monetary value to 2 decimal places."""
    return round(_safe_float(val), 2)


def _fmt(val) -> str:
    """Format a monetary float to comma-separated string with 2 dp."""
    return f"{_money(val):,.2f}"


def _build_description(rate: float, quantity=None, unit: str | None = None) -> str:
    """Build a rate description string for accommodation/DTF rows.

    - With quantity: "$ 1,488.07 x 4 Day(s)"
    - Without quantity: "$ 165.14"
    """
    rate_str = f"{_money(rate):,.2f}"
    if quantity is not None and unit:
        qty = int(quantity) if quantity == int(quantity) else quantity
        return f"$ {rate_str} x {qty} {unit}(s)"
    return f"$ {rate_str}"


def _get_ward_quantity_unit(entry: dict) -> str | None:
    """Get the ward quantity unit from a ward_breakdown entry."""
    unit = entry.get("ward_quantity_unit")
    if unit and isinstance(unit, str):
        return unit.strip().lower() or None
    return None


def _get_length_of_stay(entry: dict) -> float:
    """Get length of stay from a ward_breakdown entry.

    Falls back to computing from ward_charges / ward_unit_cost_first_block.
    """
    los = _safe_float(entry.get("length_of_stay"))
    if los > 0:
        return los
    rate = _safe_float(entry.get("ward_unit_cost_first_block"))
    charges = _safe_float(entry.get("ward_charges"))
    if rate > 0:
        return round(charges / rate, 2)
    return 0


def _build_accommodation_rows(output: dict, template_info: dict) -> list[dict]:
    """Build render-ready accommodation rows based on template scenario.

    Returns list of {"label": str, "description": str, "amount": str} dicts.
    """
    template_id = template_info["template_id"]
    ward_breakdown = output.get("ward_breakdown", []) or []
    rows = []

    if template_id == 1:
        # Ward + OR: ward accommodation + OR first block + OR subq (if exists)
        entry = ward_breakdown[0]
        rows.append(_accom_ward_first(entry))
        rows.append(_accom_or_first(output))
        if _safe_float(output.get("or_unit_cost_subq")) > 0:
            rows.append(_accom_or_subq(output))

    elif template_id == 2:
        # Ward only (days): single ward row with rate x days
        rows.append(_accom_ward_first(ward_breakdown[0]))

    elif template_id == 3:
        # Ward only (hours, 1 block): single ward row, flat rate
        rows.append(_accom_ward_first(ward_breakdown[0]))

    elif template_id == 4:
        # Ward only (hours, 2 blocks): first block + subq block
        rows.append(_accom_ward_first(ward_breakdown[0]))
        rows.append(_accom_ward_subq(ward_breakdown[0], output))

    elif template_id == 5:
        # Ward only (2 types): one row per ward type
        rows.append(_accom_ward_first(ward_breakdown[0]))
        if len(ward_breakdown) > 1:
            rows.append(_accom_ward_first(ward_breakdown[1]))

    elif template_id == 6:
        # OR only (1 block)
        rows.append(_accom_or_first(output))

    elif template_id == 7:
        # OR only (2 blocks): first block + subq
        rows.append(_accom_or_first(output))
        rows.append(_accom_or_subq(output))

    return rows


def _accom_ward_first(entry: dict) -> dict:
    """Build accommodation row for a ward first-block entry."""
    ward_type = entry.get("ward_type", "")
    rate = _money(entry.get("ward_unit_cost_first_block"))
    total = _money(entry.get("ward_charges"))
    unit = _get_ward_quantity_unit(entry)

    if unit == "days":
        los = _get_length_of_stay(entry)
        description = _build_description(rate, los, "Day")
    else:
        # Hours-based: flat rate, no multiplier
        description = _build_description(rate)

    return {"label": ward_type, "description": description, "amount": _fmt(total)}


def _accom_ward_subq(entry: dict, output: dict) -> dict:
    """Build accommodation row for a ward subsequent-block entry."""
    ward_type = entry.get("ward_type", "")
    ward_unit_cost_subq = _safe_float(output.get("ward_unit_cost_subq"))
    rate = _money(ward_unit_cost_subq)
    qty = _safe_float(output.get("ward_quantity_subq_1"))
    total = _money(rate * qty)
    label = f"{ward_type}-PER SUBQ" if ward_unit_cost_subq > 0 else ward_type
    description = _build_description(rate, qty, "Hour")

    return {"label": label, "description": description, "amount": _fmt(total)}


def _accom_or_first(output: dict) -> dict:
    """Build accommodation row for OR first-block."""
    or_type = output.get("or_type", "")
    charging_hours = output.get("or_charging_block_hours")
    rate = _money(output.get("or_unit_cost_first_block"))

    if charging_hours:
        label = f"{or_type} (First {int(charging_hours)} Hours)"
    else:
        label = or_type

    description = _build_description(rate)
    return {"label": label, "description": description, "amount": _fmt(rate)}


def _accom_or_subq(output: dict) -> dict:
    """Build accommodation row for OR subsequent-block."""
    or_type = output.get("or_type", "")
    rate = _money(output.get("or_unit_cost_subq"))
    qty = _safe_float(output.get("or_quantity_subq_1"))
    total = _money(rate * qty)
    label = f"{or_type} (Subsequent Hour or Part Thereof)"
    description = _build_description(rate, qty, "Hour")

    return {"label": label, "description": description, "amount": _fmt(total)}


def _build_dtf_rows(output: dict, template_info: dict) -> list[dict]:
    """Build render-ready DTF rows from ward_dtf_total and or_dtf.

    Returns list of {"label": str, "description": str, "amount": str} dicts.
    """
    rows = []
    ward_breakdown = output.get("ward_breakdown", []) or []

    if template_info["has_ward"]:
        for entry in ward_breakdown:
            dtf_total = _safe_float(entry.get("ward_dtf_total"))
            if dtf_total <= 0:
                continue
            unit = _get_ward_quantity_unit(entry)
            if unit == "days":
                los = _get_length_of_stay(entry)
                rate = _money(dtf_total / los) if los > 0 else _money(dtf_total)
                description = _build_description(rate, los, "Day")
                label = entry.get("ward_type", "")
            else:
                description = _build_description(dtf_total)
                label = DTF_FLAT_LABEL
            rows.append({
                "label": label,
                "description": description,
                "amount": _fmt(dtf_total),
            })

    if template_info["has_or"]:
        or_dtf = _safe_float(output.get("or_dtf"))
        if or_dtf > 0:
            rows.append({
                "label": DTF_FLAT_LABEL,
                "description": _build_description(or_dtf),
                "amount": _fmt(or_dtf),
            })

    return rows


def _compute_ancillary(output: dict, has_ward: bool) -> float:
    """Compute ancillary charges.

    If ward exists: ancillary_charges_llm + doctor_prescribed_charges + or_charges
    If OR-only:     ancillary_charges_llm + doctor_prescribed_charges
    """
    ancillary = _safe_float(output.get("ancillary_charges_llm"))
    prescribed = _safe_float(output.get("doctor_prescribed_charges"))
    or_charges = _safe_float(output.get("or_charges")) if has_ward else 0.0
    return _money(ancillary + prescribed + or_charges)


def map_fc_fields(output: dict, template_info: dict, s3_key: str = "",
                   fa_number: str | None = None) -> dict:
    """Map SageMaker output to render-ready FC form structure.

    Args:
        output: Parsed SageMaker async inference JSON output.
        template_info: Result from determine_template().
        s3_key: S3 key of the original .out file.
        fa_number: Financial assistance number looked up from inference jobs table.

    Returns:
        dict: Render-ready FC form data. All monetary values are formatted strings.
    """
    # Doctor's fees (internal floats for totals computation)
    consultation_fee = _money(output.get("consultation_fee"))
    procedure_fee = _money(output.get("procedure_fee"))
    anaesthetist_fee = _money(output.get("anaesthetist_fee"))
    assistant_surgeon_fee = 0.0
    total_doctors_fees = _money(
        consultation_fee + procedure_fee + anaesthetist_fee + assistant_surgeon_fee
    )

    # Accommodation rows
    accommodation_rows = _build_accommodation_rows(output, template_info)

    # DTF rows
    dtf_rows = _build_dtf_rows(output, template_info)

    # Ancillary
    ancillary_charges = _compute_ancillary(output, template_info["has_ward"])

    # Hospital charges total
    accom_total = sum(
        _safe_float(row["amount"].replace(",", "")) for row in accommodation_rows
    )
    dtf_total = sum(
        _safe_float(row["amount"].replace(",", "")) for row in dtf_rows
    )
    total_hospital_charges = _money(accom_total + dtf_total + ancillary_charges)

    # Grand totals
    total_estimated_amount = _money(total_doctors_fees + total_hospital_charges)
    estimated_medisave = _money(output.get("estimated_medisave_claimable"))
    deposit_required = _money(total_estimated_amount - estimated_medisave)

    # Extract job_id from S3 key
    job_id = s3_key.rsplit("/", 1)[-1].replace(".out", "") if s3_key else ""

    return {
        "job_id": job_id,
        "fa_number": fa_number,
        "template_id": template_info["template_id"],
        "template_name": template_info["template_name"],

        "doctors_fees": {
            "rows": [
                {"label": "Consultation Fee(s)", "amount": _fmt(consultation_fee)},
                {"label": "Procedure / Surgeon Fee(s)", "amount": _fmt(procedure_fee)},
                {"label": "Assistant Surgeon Fee(s)", "amount": _fmt(assistant_surgeon_fee)},
                {"label": "Anaesthetist Fee(s)", "amount": _fmt(anaesthetist_fee)},
            ],
            "total": _fmt(total_doctors_fees),
            "moh_benchmark": "N/A",
        },

        "hospital_charges": {
            "accommodation_rows": accommodation_rows,
            "dtf_rows": dtf_rows,
            "ancillary_charges": _fmt(ancillary_charges),
            "daily_companion_rate": "0.00",
            "total": _fmt(total_hospital_charges),
        },

        "totals": {
            "total_doctors_charges": _fmt(total_doctors_fees),
            "total_doctors_charges_moh": "N/A",
            "total_hospital_charges": _fmt(total_hospital_charges),
            "total_estimated_amount": _fmt(total_estimated_amount),
            "estimated_medisave_claimable": _fmt(estimated_medisave),
            "deposit_required": _fmt(deposit_required),
        },

        # Metadata
        "consumables_list": output.get("consumables_list", []),
        "flags": {
            "backup_logic": bool(output.get("backup_logic_flag")),
            "manual": bool(output.get("manual_flag")),
            "warning": bool(output.get("warning_flag")),
            "patched": bool(output.get("patched_flag")),
        },
        "raw_output_s3_key": s3_key,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
