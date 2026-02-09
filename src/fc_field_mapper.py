"""
FC Field Mapper

Maps raw SageMaker async inference output to FC form fields using
conditional logic defined in the FC sheet.

Key mapping rules:
- Accommodation 1: ward-first, OR-fallback
- Accommodation 2: second ward type or OR-fallback
- Ancillary: includes doctor_prescribed_charges; adds or_charges only if ward exists
- DTF: from ward_dtf_total or OR dtf
- Totals: doctor's fees + hospital charges; deposit = total - medisave

Rounding policy: all monetary values are rounded to 2 decimal places at the
per-field level using banker's rounding (round half to even).
"""

from datetime import datetime, timezone


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


def _build_accommodation_ward(ward_entry: dict, output: dict, is_subq: bool = False) -> dict:
    """Build accommodation slot from a ward_breakdown entry.

    For the first block: uses ward_unit_cost_first_block and ward_type.
    For subq blocks: appends '-PER SUBQ' to ward_type if subq cost > 0.
    """
    ward_type = ward_entry.get("ward_type", "")
    ward_unit_cost_subq = _safe_float(output.get("ward_unit_cost_subq"))

    if is_subq:
        rate = _money(ward_unit_cost_subq)
        qty = _safe_float(output.get("ward_quantity_subq_1"))
        return {
            "room_type": f"{ward_type}-PER SUBQ" if ward_unit_cost_subq > 0 else ward_type,
            "room_rate": rate,
            "quantity": qty,
            "total": _money(rate * qty),
        }

    return {
        "room_type": ward_type,
        "room_rate": _money(ward_entry.get("ward_unit_cost_first_block")),
        "quantity": None,
        "total": _money(ward_entry.get("ward_charges")),
    }


def _build_accommodation_or(output: dict, is_subq: bool = False) -> dict:
    """Build accommodation slot from OR data.

    For the first block: uses or_unit_cost_first_block and or_type.
    For subq blocks: appends '(Subsequent Hour or Part Thereof)'.
    """
    or_type = output.get("or_type", "")
    or_unit_cost_subq = _safe_float(output.get("or_unit_cost_subq"))

    if is_subq:
        rate = _money(or_unit_cost_subq)
        qty = _safe_float(output.get("or_quantity_subq_1"))
        return {
            "room_type": f"{or_type} (Subsequent Hour or Part Thereof)",
            "room_rate": rate,
            "quantity": qty,
            "total": _money(rate * qty),
        }

    charging_hours = output.get("or_charging_block_hours")
    if charging_hours:
        room_type = f"{or_type} (First {int(charging_hours)} Hours)"
    else:
        room_type = or_type

    return {
        "room_type": room_type,
        "room_rate": _money(output.get("or_unit_cost_first_block")),
        "quantity": None,
        "total": _money(output.get("or_unit_cost_first_block")),
    }


def _compute_ancillary(output: dict, has_ward: bool) -> float:
    """Compute ancillary charges.

    If ward exists: ancillary_charges_llm + doctor_prescribed_charges + or_charges
    If OR-only:     ancillary_charges_llm + doctor_prescribed_charges
    """
    ancillary = _safe_float(output.get("ancillary_charges_llm"))
    prescribed = _safe_float(output.get("doctor_prescribed_charges"))
    or_charges = _safe_float(output.get("or_charges")) if has_ward else 0.0
    return _money(ancillary + prescribed + or_charges)


def _build_accommodations(output: dict, template_info: dict) -> tuple:
    """Build accommodation slots based on template scenario.

    Returns:
        Tuple of (accommodation_1, accommodation_2, accommodation_3).
        Unused slots are None.
    """
    template_id = template_info["template_id"]
    ward_breakdown = output.get("ward_breakdown", []) or []

    if template_id == 1:
        # Ward + OR: ward accommodation + OR first block + OR subq (if exists)
        acc1 = _build_accommodation_ward(ward_breakdown[0], output)
        acc2 = _build_accommodation_or(output, is_subq=False)
        or_subq_cost = _safe_float(output.get("or_unit_cost_subq"))
        acc3 = _build_accommodation_or(output, is_subq=True) if or_subq_cost > 0 else None
        return (acc1, acc2, acc3)

    elif template_id == 2:
        # Ward only (days): single ward line with rate x days
        acc1 = _build_accommodation_ward(ward_breakdown[0], output)
        return (acc1, None, None)

    elif template_id == 3:
        # Ward only (hours, 1 block): single ward line
        acc1 = _build_accommodation_ward(ward_breakdown[0], output)
        return (acc1, None, None)

    elif template_id == 4:
        # Ward only (hours, 2 blocks): first block + subq block
        acc1 = _build_accommodation_ward(ward_breakdown[0], output)
        acc2 = _build_accommodation_ward(ward_breakdown[0], output, is_subq=True)
        return (acc1, acc2, None)

    elif template_id == 5:
        # Ward only (2 types): multiple ward types
        acc1 = _build_accommodation_ward(ward_breakdown[0], output)
        acc2 = _build_accommodation_ward(ward_breakdown[1], output) if len(ward_breakdown) > 1 else None
        return (acc1, acc2, None)

    elif template_id == 6:
        # OR only (1 block)
        acc1 = _build_accommodation_or(output, is_subq=False)
        return (acc1, None, None)

    elif template_id == 7:
        # OR only (2 blocks): first block + subq
        acc1 = _build_accommodation_or(output, is_subq=False)
        acc2 = _build_accommodation_or(output, is_subq=True)
        return (acc1, acc2, None)

    return (None, None, None)


def map_fc_fields(output: dict, template_info: dict, s3_key: str = "") -> dict:
    """Map SageMaker output to FC form data structure.

    Args:
        output: Parsed SageMaker async inference JSON output.
        template_info: Result from determine_template().
        s3_key: S3 key of the original .out file.

    Returns:
        dict: FC form data ready for DynamoDB storage.
    """
    # Doctor's fees
    consultation_fee = _money(output.get("consultation_fee"))
    procedure_fee = _money(output.get("procedure_fee"))
    anaesthetist_fee = _money(output.get("anaesthetist_fee"))
    assistant_surgeon_fee = 0.0  # manual field, default 0
    total_doctors_fees = _money(
        consultation_fee + procedure_fee + anaesthetist_fee + assistant_surgeon_fee
    )

    # Accommodations (always returns 3-tuple)
    accommodation_1, accommodation_2, accommodation_3 = _build_accommodations(output, template_info)

    # DTF
    daily_treatment_fee = _money(output.get("dtf"))

    # Ancillary
    ancillary_charges = _compute_ancillary(output, template_info["has_ward"])

    # Hospital charges total
    accom_total = 0.0
    for acc in [accommodation_1, accommodation_2, accommodation_3]:
        if acc:
            accom_total += _safe_float(acc.get("total"))
    total_hospital_charges = _money(accom_total + daily_treatment_fee + ancillary_charges)

    # Grand totals
    total_estimated_amount = _money(total_doctors_fees + total_hospital_charges)
    estimated_medisave = _money(output.get("estimated_medisave_claimable"))
    deposit_required = _money(total_estimated_amount - estimated_medisave)

    # Extract job_id from S3 key (e.g., "output/abc123.out" -> "abc123")
    job_id = s3_key.rsplit("/", 1)[-1].replace(".out", "") if s3_key else ""

    return {
        "job_id": job_id,
        "template_id": template_info["template_id"],
        "template_name": template_info["template_name"],

        # Doctor's Fees
        "consultation_fee": consultation_fee,
        "procedure_fee": procedure_fee,
        "anaesthetist_fee": anaesthetist_fee,
        "assistant_surgeon_fee": assistant_surgeon_fee,
        "total_doctors_fees": total_doctors_fees,

        # Hospital Charges - Accommodations
        "accommodation_1": accommodation_1,
        "accommodation_2": accommodation_2,
        "accommodation_3": accommodation_3,

        # Hospital Charges - Other
        "daily_treatment_fee": daily_treatment_fee,
        "ancillary_charges": ancillary_charges,
        "total_hospital_charges": total_hospital_charges,

        # Totals
        "total_estimated_amount": total_estimated_amount,
        "estimated_medisave_claimable": estimated_medisave,
        "deposit_required": deposit_required,

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
