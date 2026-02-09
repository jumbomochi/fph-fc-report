"""Tests for FC field mapper."""

from unittest.mock import patch
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


class TestDoctorsFees:
    def test_all_fees_summed(self):
        output = {
            "consultation_fee": 100.0,
            "procedure_fee": 200.0,
            "anaesthetist_fee": 50.0,
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 806.42,
                 "ward_charges": 806.42}
            ],
            "dtf": 333.03,
            "ancillary_charges_llm": 1500.0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1, ward_unit="days")
        result = map_fc_fields(output, template)
        assert result["consultation_fee"] == 100.0
        assert result["procedure_fee"] == 200.0
        assert result["anaesthetist_fee"] == 50.0
        assert result["assistant_surgeon_fee"] == 0.0
        assert result["total_doctors_fees"] == 350.0

    def test_missing_fees_default_to_zero(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100}
            ],
            "dtf": 0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["total_doctors_fees"] == 0.0

    def test_none_fees_default_to_zero(self):
        output = {
            "consultation_fee": None,
            "procedure_fee": None,
            "anaesthetist_fee": None,
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100}
            ],
            "dtf": 0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["total_doctors_fees"] == 0.0


class TestTemplate2WardOnlyDays:
    """Template 2: matches PDF (2) Ward only_days — Private, $806.42 x 1 Day."""

    def test_accommodation_and_totals(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 806.42,
                 "ward_charges": 806.42}
            ],
            "dtf": 333.03,
            "ancillary_charges_llm": 1500.0,
            "estimated_medisave_claimable": 1130.0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1, ward_unit="days")
        result = map_fc_fields(output, template, s3_key="output/test-job.out")

        assert result["job_id"] == "test-job"
        acc1 = result["accommodation_1"]
        assert acc1["room_type"] == "Private"
        assert acc1["room_rate"] == 806.42
        assert acc1["total"] == 806.42
        assert result["accommodation_2"] is None
        assert result["accommodation_3"] is None
        assert result["daily_treatment_fee"] == 333.03
        assert result["ancillary_charges"] == 1500.0
        # Hospital: 806.42 + 333.03 + 1500.0 = 2639.45
        assert result["total_hospital_charges"] == 2639.45
        assert result["total_estimated_amount"] == 2639.45
        assert result["estimated_medisave_claimable"] == 1130.0
        assert result["deposit_required"] == 1509.45


class TestTemplate1WardAndOR:
    """Template 1: matches PDF (1) Ward_days_OR_2blocks."""

    def test_three_accommodation_slots(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "P5 Private Deluxe", "ward_unit_cost_first_block": 1488.07,
                 "ward_charges": 5952.28}
            ],
            "or_type": "Day Surgery Suite",
            "or_unit_cost_first_block": 151.38,
            "or_unit_cost_subq": 68.81,
            "or_quantity_subq_1": 3,
            "or_charging_block_hours": 3,
            "or_charges": 500.0,
            "dtf": 1332.12,
            "ancillary_charges_llm": 3980.0,
            "estimated_medisave_claimable": 3310.0,
        }
        template = _make_template_info(1, has_ward=True, has_or=True, ward_count=1)
        result = map_fc_fields(output, template)

        acc1 = result["accommodation_1"]
        assert acc1["room_type"] == "P5 Private Deluxe"
        assert acc1["total"] == 5952.28

        acc2 = result["accommodation_2"]
        assert acc2["room_type"] == "Day Surgery Suite (First 3 Hours)"
        assert acc2["room_rate"] == 151.38

        acc3 = result["accommodation_3"]
        assert acc3["room_type"] == "Day Surgery Suite (Subsequent Hour or Part Thereof)"
        assert acc3["room_rate"] == 68.81
        assert acc3["quantity"] == 3
        assert acc3["total"] == 206.43

        # Ancillary when ward exists: llm + prescribed + or_charges
        assert result["ancillary_charges"] == 3980.0 + 500.0

    def test_no_or_subq_gives_two_slots(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100}
            ],
            "or_type": "Suite",
            "or_unit_cost_first_block": 50,
            "or_unit_cost_subq": 0,
            "or_charges": 30,
            "dtf": 0,
        }
        template = _make_template_info(1, has_ward=True, has_or=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["accommodation_3"] is None


class TestTemplate4WardHours2Blocks:
    """Template 4: matches PDF (4) Ward only_hours_2 blocks."""

    def test_subq_accommodation(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "DAY SUITE BED (4-BED)", "ward_unit_cost_first_block": 123.85,
                 "ward_charges": 123.85, "ward_quantity_unit": "hours"},
            ],
            "ward_unit_cost_subq": 55.05,
            "ward_quantity_subq_1": 3,
            "dtf": 158.72,
            "ancillary_charges_llm": 1000.0,
            "estimated_medisave_claimable": 0,
        }
        template = _make_template_info(4, has_ward=True, ward_count=2, ward_unit="hours")
        result = map_fc_fields(output, template)

        acc1 = result["accommodation_1"]
        assert acc1["room_type"] == "DAY SUITE BED (4-BED)"
        assert acc1["room_rate"] == 123.85

        acc2 = result["accommodation_2"]
        assert acc2["room_type"] == "DAY SUITE BED (4-BED)-PER SUBQ"
        assert acc2["room_rate"] == 55.05
        assert acc2["quantity"] == 3
        assert acc2["total"] == 165.15


class TestTemplate5WardOnly2Types:
    """Template 5: matches PDF (5) Ward only_2 types."""

    def test_two_ward_types(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "P5 Private Deluxe", "ward_unit_cost_first_block": 1488.07,
                 "ward_charges": 1488.07},
                {"ward_type": "Day Surgery Suite", "ward_unit_cost_first_block": 151.38,
                 "ward_charges": 151.38},
            ],
            "dtf": 491.75,
            "ancillary_charges_llm": 3000.0,
            "estimated_medisave_claimable": 2260.0,
        }
        template = _make_template_info(5, has_ward=True, ward_count=2)
        result = map_fc_fields(output, template)

        assert result["accommodation_1"]["room_type"] == "P5 Private Deluxe"
        assert result["accommodation_2"]["room_type"] == "Day Surgery Suite"


class TestTemplate6OROnly1Block:
    """Template 6: matches PDF (6) OR only_1 block."""

    def test_or_accommodation(self):
        output = {
            "or_type": "Cardiovascular Suite",
            "or_unit_cost_first_block": 165.14,
            "or_unit_cost_subq": 0,
            "or_charging_block_hours": 4,
            "dtf": 158.72,
            "ancillary_charges_llm": 2740.0,
            "estimated_medisave_claimable": 1080.0,
        }
        template = _make_template_info(6, has_or=True)
        result = map_fc_fields(output, template)

        acc1 = result["accommodation_1"]
        assert acc1["room_type"] == "Cardiovascular Suite (First 4 Hours)"
        assert acc1["room_rate"] == 165.14
        assert result["accommodation_2"] is None
        # OR-only: ancillary = llm + prescribed, no or_charges added
        assert result["ancillary_charges"] == 2740.0
        # 165.14 + 158.72 + 2740.0 = 3063.86
        assert result["total_hospital_charges"] == 3063.86
        assert result["deposit_required"] == 1983.86


class TestTemplate7OROnly2Blocks:
    """Template 7: matches PDF (7) OR only_2 blocks."""

    def test_or_two_blocks(self):
        output = {
            "or_type": "Cardiovascular Suite",
            "or_unit_cost_first_block": 165.14,
            "or_unit_cost_subq": 55.05,
            "or_quantity_subq_1": 1,
            "or_charging_block_hours": 4,
            "dtf": 158.72,
            "ancillary_charges_llm": 2740.0,
            "estimated_medisave_claimable": 1080.0,
        }
        template = _make_template_info(7, has_or=True)
        result = map_fc_fields(output, template)

        acc1 = result["accommodation_1"]
        assert acc1["room_type"] == "Cardiovascular Suite (First 4 Hours)"
        acc2 = result["accommodation_2"]
        assert acc2["room_type"] == "Cardiovascular Suite (Subsequent Hour or Part Thereof)"
        assert acc2["room_rate"] == 55.05
        assert acc2["quantity"] == 1
        assert acc2["total"] == 55.05


class TestRounding:
    def test_monetary_values_rounded_to_2dp(self):
        output = {
            "consultation_fee": 100.555,
            "procedure_fee": 200.444,
            "anaesthetist_fee": 50.005,
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100.999,
                 "ward_charges": 100.999}
            ],
            "dtf": 33.333,
            "ancillary_charges_llm": 10.001,
            "estimated_medisave_claimable": 5.555,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["consultation_fee"] == 100.56  # round half to even
        assert result["procedure_fee"] == 200.44
        assert result["anaesthetist_fee"] == 50.01  # 50.005 -> 50.01 (float repr)
        assert result["daily_treatment_fee"] == 33.33
        assert result["accommodation_1"]["room_rate"] == 101.0
        assert result["estimated_medisave_claimable"] == 5.55  # 5.555 as float -> 5.554... -> 5.55


class TestAncillaryConditional:
    def test_ward_present_includes_or_charges(self):
        output = {
            "ancillary_charges_llm": 100,
            "doctor_prescribed_charges": 50,
            "or_charges": 30,
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100}
            ],
            "dtf": 0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["ancillary_charges"] == 180.0  # 100 + 50 + 30

    def test_or_only_excludes_or_charges(self):
        output = {
            "ancillary_charges_llm": 100,
            "doctor_prescribed_charges": 50,
            "or_charges": 30,
            "or_type": "Suite",
            "or_unit_cost_first_block": 165,
            "or_unit_cost_subq": 0,
            "dtf": 0,
        }
        template = _make_template_info(6, has_ward=False, has_or=True)
        result = map_fc_fields(output, template)
        assert result["ancillary_charges"] == 150.0  # 100 + 50, no or_charges


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


class TestNegativeAndEdgeCases:
    def test_negative_fee_values_kept(self):
        """Negative values are passed through (may indicate credit/adjustment)."""
        output = {
            "consultation_fee": -10.0,
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100}
            ],
            "dtf": 0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["consultation_fee"] == -10.0
        assert result["total_doctors_fees"] == -10.0

    def test_string_numeric_values_converted(self):
        output = {
            "consultation_fee": "150.50",
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": "100",
                 "ward_charges": "100"}
            ],
            "dtf": "33.33",
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["consultation_fee"] == 150.5
        assert result["daily_treatment_fee"] == 33.33

    def test_invalid_string_defaults_to_zero(self):
        output = {
            "consultation_fee": "not_a_number",
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100}
            ],
            "dtf": 0,
        }
        template = _make_template_info(2, has_ward=True, ward_count=1)
        result = map_fc_fields(output, template)
        assert result["consultation_fee"] == 0.0

    def test_template_0_unclassified_produces_output(self):
        output = {"dtf": 50, "ancillary_charges_llm": 10}
        template = _make_template_info(0)
        result = map_fc_fields(output, template)
        assert result["accommodation_1"] is None
        assert result["daily_treatment_fee"] == 50.0
        assert result["total_hospital_charges"] == 60.0
