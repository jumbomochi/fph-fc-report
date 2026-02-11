"""Tests for FC field mapper — render-ready output."""

from src.fc_field_mapper import map_fc_fields


def _make_template_info(template_id, template_name="Test", has_ward=False, has_or=False,
                        ward_count=0, ward_unit=None):
    return {
        "template_id": template_id,
        "template_name": template_name,
        "has_ward": has_ward,
        "has_or": has_or,
        "ward_count": ward_count,
        "ward_unit": ward_unit,
    }


# ---------------------------------------------------------------------------
# Doctor's Fees
# ---------------------------------------------------------------------------

class TestDoctorsFees:
    def test_all_fees_summed(self):
        output = {
            "consultation_fee": 100.0,
            "procedure_fee": 200.0,
            "anaesthetist_fee": 50.0,
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 806.42,
                 "ward_charges": 806.42, "ward_quantity_unit": "days",
                 "length_of_stay": 1, "ward_dtf_total": 333.03}
            ],
            "dtf": 333.03,
            "ancillary_charges_llm": 1500.0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1, ward_unit="days")
        result = map_fc_fields(output, template)
        fees = result["doctors_fees"]
        assert fees["rows"][0] == {"label": "Consultation Fee(s)", "amount": "100.00"}
        assert fees["rows"][1] == {"label": "Procedure / Surgeon Fee(s)", "amount": "200.00"}
        assert fees["rows"][2] == {"label": "Assistant Surgeon Fee(s)", "amount": "0.00"}
        assert fees["rows"][3] == {"label": "Anaesthetist Fee(s)", "amount": "50.00"}
        assert fees["total"] == "350.00"
        assert fees["moh_benchmark"] == "N/A"

    def test_missing_fees_default_to_zero(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100, "ward_quantity_unit": "days",
                 "length_of_stay": 1, "ward_dtf_total": 0}
            ],
            "dtf": 0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["doctors_fees"]["total"] == "0.00"

    def test_none_fees_default_to_zero(self):
        output = {
            "consultation_fee": None,
            "procedure_fee": None,
            "anaesthetist_fee": None,
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100, "ward_quantity_unit": "days",
                 "length_of_stay": 1, "ward_dtf_total": 0}
            ],
            "dtf": 0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["doctors_fees"]["total"] == "0.00"


# ---------------------------------------------------------------------------
# Template 2: Ward only (days) — matches PDF (2)
# ---------------------------------------------------------------------------

class TestTemplate2WardOnlyDays:
    def test_accommodation_and_totals(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 806.42,
                 "ward_charges": 806.42, "ward_quantity_unit": "days",
                 "length_of_stay": 1, "ward_dtf_total": 333.03}
            ],
            "dtf": 333.03,
            "ancillary_charges_llm": 1500.0,
            "estimated_medisave_claimable": 1130.0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1, ward_unit="days")
        result = map_fc_fields(output, template, s3_key="output/test-job.out")

        assert result["job_id"] == "test-job"

        # Accommodation: 1 row
        acc = result["hospital_charges"]["accommodation_rows"]
        assert len(acc) == 1
        assert acc[0]["label"] == "Private"
        assert acc[0]["description"] == "$ 806.42 x 1 Day(s)"
        assert acc[0]["amount"] == "806.42"

        # DTF: 1 row
        dtf = result["hospital_charges"]["dtf_rows"]
        assert len(dtf) == 1
        assert dtf[0]["label"] == "Private"
        assert dtf[0]["description"] == "$ 333.03 x 1 Day(s)"
        assert dtf[0]["amount"] == "333.03"

        # Hospital charges: 806.42 + 333.03 + 1500.0 = 2639.45
        assert result["hospital_charges"]["ancillary_charges"] == "1,500.00"
        assert result["hospital_charges"]["total"] == "2,639.45"
        assert result["totals"]["total_estimated_amount"] == "2,639.45"
        assert result["totals"]["estimated_medisave_claimable"] == "1,130.00"
        assert result["totals"]["deposit_required"] == "1,509.45"


# ---------------------------------------------------------------------------
# Template 1: Ward + OR — matches PDF (1) Ward_days_OR_2blocks
# ---------------------------------------------------------------------------

class TestTemplate1WardAndOR:
    def test_three_accommodation_rows(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "P5 Private Deluxe", "ward_unit_cost_first_block": 1488.07,
                 "ward_charges": 5952.28, "ward_quantity_unit": "days",
                 "length_of_stay": 4, "ward_dtf_total": 1332.12}
            ],
            "or_type": "Day Surgery Suite",
            "or_unit_cost_first_block": 151.38,
            "or_unit_cost_subq": 68.81,
            "or_quantity_subq_1": 3,
            "or_charging_block_hours": 3,
            "or_charges": 500.0,
            "or_dtf": 0,
            "dtf": 1332.12,
            "ancillary_charges_llm": 3980.0,
            "estimated_medisave_claimable": 3310.0,
        }
        template = _make_template_info(1, has_ward=True, has_or=True, ward_count=1)
        result = map_fc_fields(output, template)

        acc = result["hospital_charges"]["accommodation_rows"]
        assert len(acc) == 3

        assert acc[0]["label"] == "P5 Private Deluxe"
        assert acc[0]["description"] == "$ 1,488.07 x 4 Day(s)"
        assert acc[0]["amount"] == "5,952.28"

        assert acc[1]["label"] == "Day Surgery Suite (First 3 Hours)"
        assert acc[1]["description"] == "$ 151.38"
        assert acc[1]["amount"] == "151.38"

        assert acc[2]["label"] == "Day Surgery Suite (Subsequent Hour or Part Thereof)"
        assert acc[2]["description"] == "$ 68.81 x 3 Hour(s)"
        assert acc[2]["amount"] == "206.43"

        # DTF: 1 ward row
        dtf = result["hospital_charges"]["dtf_rows"]
        assert len(dtf) == 1
        assert dtf[0]["label"] == "P5 Private Deluxe"
        assert dtf[0]["description"] == "$ 333.03 x 4 Day(s)"
        assert dtf[0]["amount"] == "1,332.12"

        # Ancillary when ward exists: llm + prescribed + or_charges
        assert result["hospital_charges"]["ancillary_charges"] == "4,480.00"

    def test_no_or_subq_gives_two_rows(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100, "ward_quantity_unit": "days",
                 "length_of_stay": 1, "ward_dtf_total": 50}
            ],
            "or_type": "Suite",
            "or_unit_cost_first_block": 50,
            "or_unit_cost_subq": 0,
            "or_charges": 30,
            "or_dtf": 0,
            "dtf": 50,
        }
        template = _make_template_info(1, has_ward=True, has_or=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert len(result["hospital_charges"]["accommodation_rows"]) == 2

    def test_ward_plus_or_with_or_dtf(self):
        """When both ward and OR have DTF, both rows should appear."""
        output = {
            "ward_breakdown": [
                {"ward_type": "P5 Private Deluxe", "ward_unit_cost_first_block": 1488.07,
                 "ward_charges": 1488.07, "ward_quantity_unit": "days",
                 "length_of_stay": 1, "ward_dtf_total": 333.03}
            ],
            "or_type": "Day Surgery Suite",
            "or_unit_cost_first_block": 151.38,
            "or_unit_cost_subq": 0,
            "or_charging_block_hours": 3,
            "or_charges": 0,
            "or_dtf": 158.72,
            "dtf": 491.75,
        }
        template = _make_template_info(1, has_ward=True, has_or=True, ward_count=1)
        result = map_fc_fields(output, template)
        dtf = result["hospital_charges"]["dtf_rows"]
        assert len(dtf) == 2
        assert dtf[0]["label"] == "P5 Private Deluxe"
        assert dtf[0]["description"] == "$ 333.03 x 1 Day(s)"
        assert dtf[1]["label"] == "TREATMENT FEE-DAY SUITE"
        assert dtf[1]["description"] == "$ 158.72"


# ---------------------------------------------------------------------------
# Template 3: Ward only (hours, 1 block) — matches PDF (3)
# ---------------------------------------------------------------------------

class TestTemplate3WardHours1Block:
    def test_flat_rate_accommodation(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "DAY SUITE BED (4 BED)-1ST 3HR",
                 "ward_unit_cost_first_block": 123.85,
                 "ward_charges": 123.85, "ward_quantity_unit": "hours",
                 "length_of_stay": 1, "ward_dtf_total": 158.72}
            ],
            "dtf": 158.72,
            "ancillary_charges_llm": 1000.0,
        }
        template = _make_template_info(3, has_ward=True, ward_count=1, ward_unit="hours")
        result = map_fc_fields(output, template)

        acc = result["hospital_charges"]["accommodation_rows"]
        assert len(acc) == 1
        assert acc[0]["label"] == "DAY SUITE BED (4 BED)-1ST 3HR"
        assert acc[0]["description"] == "$ 123.85"
        assert acc[0]["amount"] == "123.85"

        dtf = result["hospital_charges"]["dtf_rows"]
        assert len(dtf) == 1
        assert dtf[0]["label"] == "TREATMENT FEE-DAY SUITE"
        assert dtf[0]["description"] == "$ 158.72"
        assert dtf[0]["amount"] == "158.72"


# ---------------------------------------------------------------------------
# Template 4: Ward only (hours, 2 blocks) — matches PDF (4)
# ---------------------------------------------------------------------------

class TestTemplate4WardHours2Blocks:
    def test_subq_accommodation(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "DAY SUITE BED (4-BED)", "ward_unit_cost_first_block": 123.85,
                 "ward_charges": 123.85, "ward_quantity_unit": "hours",
                 "length_of_stay": 1, "ward_dtf_total": 158.72},
            ],
            "ward_unit_cost_subq": 55.05,
            "ward_quantity_subq_1": 3,
            "dtf": 158.72,
            "ancillary_charges_llm": 1000.0,
            "estimated_medisave_claimable": 0,
        }
        template = _make_template_info(4, has_ward=True, ward_count=2, ward_unit="hours")
        result = map_fc_fields(output, template)

        acc = result["hospital_charges"]["accommodation_rows"]
        assert len(acc) == 2
        assert acc[0]["label"] == "DAY SUITE BED (4-BED)"
        assert acc[0]["description"] == "$ 123.85"
        assert acc[0]["amount"] == "123.85"

        assert acc[1]["label"] == "DAY SUITE BED (4-BED)-PER SUBQ"
        assert acc[1]["description"] == "$ 55.05 x 3 Hour(s)"
        assert acc[1]["amount"] == "165.15"


# ---------------------------------------------------------------------------
# Template 5: Ward only (2 types) — matches PDF (5)
# ---------------------------------------------------------------------------

class TestTemplate5WardOnly2Types:
    def test_two_ward_types(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "P5 Private Deluxe", "ward_unit_cost_first_block": 1488.07,
                 "ward_charges": 1488.07, "ward_quantity_unit": "days",
                 "length_of_stay": 1, "ward_dtf_total": 333.03},
                {"ward_type": "Day Surgery Suite", "ward_unit_cost_first_block": 151.38,
                 "ward_charges": 151.38, "ward_quantity_unit": "hours",
                 "length_of_stay": 1, "ward_dtf_total": 158.72},
            ],
            "dtf": 491.75,
            "ancillary_charges_llm": 3000.0,
            "estimated_medisave_claimable": 2260.0,
        }
        template = _make_template_info(5, has_ward=True, ward_count=2)
        result = map_fc_fields(output, template)

        acc = result["hospital_charges"]["accommodation_rows"]
        assert len(acc) == 2
        assert acc[0]["label"] == "P5 Private Deluxe"
        assert acc[0]["description"] == "$ 1,488.07 x 1 Day(s)"
        assert acc[1]["label"] == "Day Surgery Suite"
        assert acc[1]["description"] == "$ 151.38"

        # DTF: 2 rows (days ward + hours ward)
        dtf = result["hospital_charges"]["dtf_rows"]
        assert len(dtf) == 2
        assert dtf[0]["label"] == "P5 Private Deluxe"
        assert dtf[0]["description"] == "$ 333.03 x 1 Day(s)"
        assert dtf[0]["amount"] == "333.03"
        assert dtf[1]["label"] == "TREATMENT FEE-DAY SUITE"
        assert dtf[1]["description"] == "$ 158.72"
        assert dtf[1]["amount"] == "158.72"


# ---------------------------------------------------------------------------
# Template 6: OR only (1 block) — matches PDF (6)
# ---------------------------------------------------------------------------

class TestTemplate6OROnly1Block:
    def test_or_accommodation(self):
        output = {
            "or_type": "Cardiovascular Suite",
            "or_unit_cost_first_block": 165.14,
            "or_unit_cost_subq": 0,
            "or_charging_block_hours": 4,
            "or_dtf": 158.72,
            "dtf": 158.72,
            "ancillary_charges_llm": 2740.0,
            "estimated_medisave_claimable": 1080.0,
        }
        template = _make_template_info(6, has_or=True)
        result = map_fc_fields(output, template)

        acc = result["hospital_charges"]["accommodation_rows"]
        assert len(acc) == 1
        assert acc[0]["label"] == "Cardiovascular Suite (First 4 Hours)"
        assert acc[0]["description"] == "$ 165.14"
        assert acc[0]["amount"] == "165.14"

        dtf = result["hospital_charges"]["dtf_rows"]
        assert len(dtf) == 1
        assert dtf[0]["label"] == "TREATMENT FEE-DAY SUITE"
        assert dtf[0]["description"] == "$ 158.72"
        assert dtf[0]["amount"] == "158.72"

        # OR-only: ancillary = llm + prescribed, no or_charges
        assert result["hospital_charges"]["ancillary_charges"] == "2,740.00"
        # 165.14 + 158.72 + 2740.0 = 3063.86
        assert result["hospital_charges"]["total"] == "3,063.86"
        assert result["totals"]["deposit_required"] == "1,983.86"


# ---------------------------------------------------------------------------
# Template 7: OR only (2 blocks) — matches PDF (7)
# ---------------------------------------------------------------------------

class TestTemplate7OROnly2Blocks:
    def test_or_two_blocks(self):
        output = {
            "or_type": "Cardiovascular Suite",
            "or_unit_cost_first_block": 165.14,
            "or_unit_cost_subq": 55.05,
            "or_quantity_subq_1": 1,
            "or_charging_block_hours": 4,
            "or_dtf": 158.72,
            "dtf": 158.72,
            "ancillary_charges_llm": 2740.0,
            "estimated_medisave_claimable": 1080.0,
        }
        template = _make_template_info(7, has_or=True)
        result = map_fc_fields(output, template)

        acc = result["hospital_charges"]["accommodation_rows"]
        assert len(acc) == 2
        assert acc[0]["label"] == "Cardiovascular Suite (First 4 Hours)"
        assert acc[0]["description"] == "$ 165.14"
        assert acc[1]["label"] == "Cardiovascular Suite (Subsequent Hour or Part Thereof)"
        assert acc[1]["description"] == "$ 55.05 x 1 Hour(s)"
        assert acc[1]["amount"] == "55.05"


# ---------------------------------------------------------------------------
# Monetary Formatting
# ---------------------------------------------------------------------------

class TestMonetaryFormatting:
    def test_comma_separated_thousands(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "P5 Private Deluxe", "ward_unit_cost_first_block": 1488.07,
                 "ward_charges": 5952.28, "ward_quantity_unit": "days",
                 "length_of_stay": 4, "ward_dtf_total": 1332.12}
            ],
            "dtf": 1332.12,
            "ancillary_charges_llm": 4480.0,
            "estimated_medisave_claimable": 3310.0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1, ward_unit="days")
        result = map_fc_fields(output, template)

        assert result["hospital_charges"]["accommodation_rows"][0]["amount"] == "5,952.28"
        assert result["hospital_charges"]["ancillary_charges"] == "4,480.00"
        assert result["hospital_charges"]["total"] == "11,764.40"
        assert result["totals"]["estimated_medisave_claimable"] == "3,310.00"

    def test_rounding_to_2dp(self):
        output = {
            "consultation_fee": 100.555,
            "procedure_fee": 200.444,
            "anaesthetist_fee": 50.005,
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100.999,
                 "ward_charges": 100.999, "ward_quantity_unit": "days",
                 "length_of_stay": 1, "ward_dtf_total": 33.333}
            ],
            "dtf": 33.333,
            "ancillary_charges_llm": 10.001,
            "estimated_medisave_claimable": 5.555,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        # round half to even: 100.555 -> 100.56 (float repr)
        assert result["doctors_fees"]["rows"][0]["amount"] == "100.56"
        assert result["doctors_fees"]["rows"][1]["amount"] == "200.44"
        assert result["hospital_charges"]["dtf_rows"][0]["amount"] == "33.33"

    def test_zero_values_formatted(self):
        output = {"ward_breakdown": [], "dtf": 0}
        template = _make_template_info(0)
        result = map_fc_fields(output, template)
        assert result["doctors_fees"]["total"] == "0.00"
        assert result["hospital_charges"]["ancillary_charges"] == "0.00"
        assert result["totals"]["deposit_required"] == "0.00"


# ---------------------------------------------------------------------------
# Description Strings
# ---------------------------------------------------------------------------

class TestDescriptionStrings:
    def test_days_description(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 806.42,
                 "ward_charges": 3225.68, "ward_quantity_unit": "days",
                 "length_of_stay": 4, "ward_dtf_total": 1332.12}
            ],
            "dtf": 1332.12,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1, ward_unit="days")
        result = map_fc_fields(output, template)
        assert result["hospital_charges"]["accommodation_rows"][0]["description"] == "$ 806.42 x 4 Day(s)"

    def test_hours_flat_description(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Day Suite", "ward_unit_cost_first_block": 123.85,
                 "ward_charges": 123.85, "ward_quantity_unit": "hours",
                 "length_of_stay": 1, "ward_dtf_total": 158.72}
            ],
            "dtf": 158.72,
        }
        template = _make_template_info(3, has_ward=True, ward_count=1, ward_unit="hours")
        result = map_fc_fields(output, template)
        assert result["hospital_charges"]["accommodation_rows"][0]["description"] == "$ 123.85"

    def test_or_subq_description(self):
        output = {
            "or_type": "Cardiovascular Suite",
            "or_unit_cost_first_block": 165.14,
            "or_unit_cost_subq": 55.05,
            "or_quantity_subq_1": 2,
            "or_charging_block_hours": 4,
            "or_dtf": 158.72,
            "dtf": 158.72,
        }
        template = _make_template_info(7, has_or=True)
        result = map_fc_fields(output, template)
        assert result["hospital_charges"]["accommodation_rows"][1]["description"] == "$ 55.05 x 2 Hour(s)"


# ---------------------------------------------------------------------------
# Ancillary Conditional Logic
# ---------------------------------------------------------------------------

class TestAncillaryConditional:
    def test_ward_present_includes_or_charges(self):
        output = {
            "ancillary_charges_llm": 100,
            "doctor_prescribed_charges": 50,
            "or_charges": 30,
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100, "ward_quantity_unit": "days",
                 "length_of_stay": 1, "ward_dtf_total": 0}
            ],
            "dtf": 0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["hospital_charges"]["ancillary_charges"] == "180.00"

    def test_or_only_excludes_or_charges(self):
        output = {
            "ancillary_charges_llm": 100,
            "doctor_prescribed_charges": 50,
            "or_charges": 30,
            "or_type": "Suite",
            "or_unit_cost_first_block": 165,
            "or_unit_cost_subq": 0,
            "or_dtf": 0,
            "dtf": 0,
        }
        template = _make_template_info(6, has_ward=False, has_or=True)
        result = map_fc_fields(output, template)
        assert result["hospital_charges"]["ancillary_charges"] == "150.00"


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_job_id_extracted_from_s3_key(self):
        output = {"ward_breakdown": [], "dtf": 0}
        template = _make_template_info(0)
        result = map_fc_fields(output, template, s3_key="output/abc-123-def.out")
        assert result["job_id"] == "abc-123-def"

    def test_empty_s3_key(self):
        output = {"ward_breakdown": [], "dtf": 0}
        template = _make_template_info(0)
        result = map_fc_fields(output, template, s3_key="")
        assert result["job_id"] == ""

    def test_fa_number_included_when_provided(self):
        output = {"ward_breakdown": [], "dtf": 0}
        template = _make_template_info(0)
        result = map_fc_fields(output, template, fa_number="FA-99999")
        assert result["fa_number"] == "FA-99999"

    def test_fa_number_none_when_not_provided(self):
        output = {"ward_breakdown": [], "dtf": 0}
        template = _make_template_info(0)
        result = map_fc_fields(output, template)
        assert result["fa_number"] is None

    def test_flags_from_output(self):
        output = {
            "ward_breakdown": [],
            "dtf": 0,
            "backup_logic_flag": True,
            "manual_flag": False,
            "warning_flag": 1,
            "patched_flag": None,
        }
        template = _make_template_info(0)
        result = map_fc_fields(output, template)
        assert result["flags"]["backup_logic"] is True
        assert result["flags"]["manual"] is False
        assert result["flags"]["warning"] is True
        assert result["flags"]["patched"] is False

    def test_processed_at_is_iso_timestamp(self):
        output = {"ward_breakdown": [], "dtf": 0}
        template = _make_template_info(0)
        result = map_fc_fields(output, template)
        assert "T" in result["processed_at"]
        assert "+" in result["processed_at"] or "Z" in result["processed_at"]

    def test_consumables_list_passthrough(self):
        items = [{"name": "Bandage", "cost": 5.0}]
        output = {"ward_breakdown": [], "dtf": 0, "consumables_list": items}
        template = _make_template_info(0)
        result = map_fc_fields(output, template)
        assert result["consumables_list"] == items


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestNegativeAndEdgeCases:
    def test_negative_fee_values_kept(self):
        output = {
            "consultation_fee": -10.0,
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100, "ward_quantity_unit": "days",
                 "length_of_stay": 1, "ward_dtf_total": 0}
            ],
            "dtf": 0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["doctors_fees"]["rows"][0]["amount"] == "-10.00"
        assert result["doctors_fees"]["total"] == "-10.00"

    def test_string_numeric_values_converted(self):
        output = {
            "consultation_fee": "150.50",
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": "100",
                 "ward_charges": "100", "ward_quantity_unit": "days",
                 "length_of_stay": "1", "ward_dtf_total": "33.33"}
            ],
            "dtf": "33.33",
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["doctors_fees"]["rows"][0]["amount"] == "150.50"

    def test_invalid_string_defaults_to_zero(self):
        output = {
            "consultation_fee": "not_a_number",
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100, "ward_quantity_unit": "days",
                 "length_of_stay": 1, "ward_dtf_total": 0}
            ],
            "dtf": 0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["doctors_fees"]["rows"][0]["amount"] == "0.00"

    def test_template_0_unclassified_produces_output(self):
        output = {"dtf": 50, "ancillary_charges_llm": 10}
        template = _make_template_info(0)
        result = map_fc_fields(output, template)
        assert result["hospital_charges"]["accommodation_rows"] == []
        assert result["hospital_charges"]["dtf_rows"] == []
        assert result["hospital_charges"]["ancillary_charges"] == "10.00"

    def test_length_of_stay_fallback_from_charges(self):
        """When length_of_stay is missing, compute from ward_charges / rate."""
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 500.0,
                 "ward_charges": 2000.0, "ward_quantity_unit": "days",
                 "ward_dtf_total": 400.0}
                # No length_of_stay field
            ],
            "dtf": 400.0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1, ward_unit="days")
        result = map_fc_fields(output, template)
        acc = result["hospital_charges"]["accommodation_rows"]
        assert acc[0]["description"] == "$ 500.00 x 4 Day(s)"
        dtf = result["hospital_charges"]["dtf_rows"]
        assert dtf[0]["description"] == "$ 100.00 x 4 Day(s)"

    def test_daily_companion_rate_always_zero(self):
        output = {"ward_breakdown": [], "dtf": 0}
        template = _make_template_info(0)
        result = map_fc_fields(output, template)
        assert result["hospital_charges"]["daily_companion_rate"] == "0.00"
