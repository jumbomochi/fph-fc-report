"""Tests for FC template selector."""

from src.fc_template_selector import determine_template


class TestTemplate1WardAndOR:
    def test_basic(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "P5 Private Deluxe", "ward_unit_cost_first_block": 1488.07,
                 "ward_charges": 5952.28, "ward_quantity_unit": "days"}
            ],
            "or_type": "Day Surgery Suite",
            "or_charges": 500.0,
            "or_unit_cost_first_block": 151.38,
            "or_unit_cost_subq": 68.81,
        }
        result = determine_template(output)
        assert result["template_id"] == 1
        assert result["template_name"] == "Ward + OR"
        assert result["has_ward"] is True
        assert result["has_or"] is True


class TestTemplate2WardOnlyDays:
    def test_basic(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 806.42,
                 "ward_charges": 806.42, "ward_quantity_unit": "days"}
            ],
            "or_type": None,
            "or_charges": 0,
        }
        result = determine_template(output)
        assert result["template_id"] == 2
        assert result["ward_unit"] == "days"

    def test_missing_quantity_unit_defaults_to_days(self):
        """No ward_quantity_unit -> not 'hours' -> template 2."""
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100.0,
                 "ward_charges": 100.0}
            ],
            "or_type": None,
        }
        result = determine_template(output)
        assert result["template_id"] == 2


class TestTemplate3WardOnlyHours1Block:
    def test_basic(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "DAY SUITE BED (4 BED)", "ward_unit_cost_first_block": 123.85,
                 "ward_charges": 123.85, "ward_quantity_unit": "hours"}
            ],
            "or_type": None,
            "or_charges": 0,
        }
        result = determine_template(output)
        assert result["template_id"] == 3
        assert result["ward_count"] == 1
        assert result["ward_unit"] == "hours"


class TestTemplate4WardOnlyHours2Blocks:
    def test_basic(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "DAY SUITE BED (4 BED)", "ward_unit_cost_first_block": 123.85,
                 "ward_charges": 123.85, "ward_quantity_unit": "hours"},
                {"ward_type": "DAY SUITE BED (4 BED)", "ward_unit_cost_first_block": 55.05,
                 "ward_charges": 165.15, "ward_quantity_unit": "hours"},
            ],
            "ward_unit_cost_subq": 55.05,
            "or_type": None,
            "or_charges": 0,
        }
        result = determine_template(output)
        assert result["template_id"] == 4
        assert result["ward_count"] == 2


class TestTemplate5WardOnly2Types:
    def test_basic(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "P5 Private Deluxe", "ward_unit_cost_first_block": 1488.07,
                 "ward_charges": 1488.07, "ward_quantity_unit": "days"},
                {"ward_type": "Day Surgery Suite", "ward_unit_cost_first_block": 151.38,
                 "ward_charges": 151.38, "ward_quantity_unit": "hours"},
            ],
            "or_type": None,
            "or_charges": 0,
        }
        result = determine_template(output)
        assert result["template_id"] == 5

    def test_precedence_over_hours_2_blocks(self):
        """Two distinct types with hours -> template 5, not template 4."""
        output = {
            "ward_breakdown": [
                {"ward_type": "Type A", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100, "ward_quantity_unit": "hours"},
                {"ward_type": "Type B", "ward_unit_cost_first_block": 50,
                 "ward_charges": 50, "ward_quantity_unit": "hours"},
            ],
            "or_type": None,
        }
        result = determine_template(output)
        assert result["template_id"] == 5


class TestTemplate6OROnly1Block:
    def test_basic(self):
        output = {
            "ward_breakdown": [],
            "or_type": "Cardiovascular Suite",
            "or_charges": 165.14,
            "or_unit_cost_first_block": 165.14,
            "or_unit_cost_subq": 0,
            "or_charging_block_hours": 4,
        }
        result = determine_template(output)
        assert result["template_id"] == 6
        assert result["has_ward"] is False
        assert result["has_or"] is True


class TestTemplate7OROnly2Blocks:
    def test_basic(self):
        output = {
            "ward_breakdown": [],
            "or_type": "Cardiovascular Suite",
            "or_charges": 220.19,
            "or_unit_cost_first_block": 165.14,
            "or_unit_cost_subq": 55.05,
            "or_charging_block_hours": 4,
        }
        result = determine_template(output)
        assert result["template_id"] == 7


class TestUnclassifiedAndEdgeCases:
    def test_no_ward_no_or(self):
        output = {"ward_breakdown": [], "or_type": None, "or_charges": 0}
        result = determine_template(output)
        assert result["template_id"] == 0
        assert result["template_name"] == "UNCLASSIFIED"

    def test_empty_dict(self):
        result = determine_template({})
        assert result["template_id"] == 0

    def test_ward_breakdown_none(self):
        output = {
            "ward_breakdown": None,
            "or_type": "Cardiovascular Suite",
            "or_charges": 100.0,
            "or_unit_cost_first_block": 100.0,
            "or_unit_cost_subq": 0,
        }
        result = determine_template(output)
        assert result["template_id"] == 6
        assert result["has_ward"] is False

    def test_or_type_empty_string_treated_as_no_or(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100.0,
                 "ward_charges": 100.0, "ward_quantity_unit": "days"}
            ],
            "or_type": "",
            "or_charges": 0,
        }
        result = determine_template(output)
        assert result["has_or"] is False
        assert result["template_id"] == 2

    def test_or_type_whitespace_only_treated_as_no_or(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100.0,
                 "ward_charges": 100.0, "ward_quantity_unit": "days"}
            ],
            "or_type": "   ",
        }
        result = determine_template(output)
        assert result["has_or"] is False

    def test_ward_type_none_in_breakdown_ignored(self):
        output = {
            "ward_breakdown": [{"ward_type": None, "ward_charges": 0}],
            "or_type": "Cardiovascular Suite",
            "or_charges": 165.14,
            "or_unit_cost_first_block": 165.14,
            "or_unit_cost_subq": 0,
        }
        result = determine_template(output)
        assert result["has_ward"] is False
        assert result["template_id"] == 6

    def test_ward_type_empty_string_ignored(self):
        output = {
            "ward_breakdown": [{"ward_type": "", "ward_charges": 0}],
            "or_type": None,
        }
        result = determine_template(output)
        assert result["has_ward"] is False
        assert result["template_id"] == 0

    def test_or_charges_zero_unit_cost_positive(self):
        """or_charges=0 but or_unit_cost_first_block>0 still counts as has_or."""
        output = {
            "ward_breakdown": [],
            "or_type": "Suite",
            "or_charges": 0,
            "or_unit_cost_first_block": 150.0,
            "or_unit_cost_subq": 0,
        }
        result = determine_template(output)
        assert result["has_or"] is True
        assert result["template_id"] == 6

    def test_or_type_present_but_no_charges(self):
        """or_type set but both charges are 0 -> no OR."""
        output = {
            "ward_breakdown": [],
            "or_type": "Suite",
            "or_charges": 0,
            "or_unit_cost_first_block": 0,
        }
        result = determine_template(output)
        assert result["has_or"] is False
        assert result["template_id"] == 0

    def test_negative_charges_treated_as_no_or(self):
        output = {
            "ward_breakdown": [],
            "or_type": "Suite",
            "or_charges": -10,
            "or_unit_cost_first_block": -5,
        }
        result = determine_template(output)
        assert result["has_or"] is False

    def test_string_numeric_values_handled(self):
        """Numeric values passed as strings should be handled."""
        output = {
            "ward_breakdown": [],
            "or_type": "Suite",
            "or_charges": "100.5",
            "or_unit_cost_first_block": "50",
            "or_unit_cost_subq": "0",
        }
        result = determine_template(output)
        assert result["has_or"] is True
        assert result["template_id"] == 6

    def test_ward_quantity_unit_case_insensitive(self):
        output = {
            "ward_breakdown": [
                {"ward_type": "Private", "ward_unit_cost_first_block": 100,
                 "ward_charges": 100, "ward_quantity_unit": "Hours"}
            ],
            "or_type": None,
        }
        result = determine_template(output)
        assert result["template_id"] == 3
        assert result["ward_unit"] == "hours"
