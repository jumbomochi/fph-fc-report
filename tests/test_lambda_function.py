"""Tests for Lambda handler with mocked AWS services."""

import json
import os
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

# Set env vars before importing handler (boto3 needs a region at import time)
os.environ["AWS_DEFAULT_REGION"] = "ap-southeast-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["DYNAMODB_TABLE"] = "FphFCFormData-test"
os.environ["INFERENCE_JOBS_TABLE"] = "FphInferenceJobs-test"


def _make_s3_event(bucket: str, key: str) -> dict:
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key},
                }
            }
        ]
    }


def _make_multi_record_event(records: list[tuple[str, str]]) -> dict:
    return {
        "Records": [
            {"s3": {"bucket": {"name": b}, "object": {"key": k}}}
            for b, k in records
        ]
    }


SAMPLE_OUTPUT_WARD_DAYS = {
    "ward_breakdown": [
        {"ward_type": "Private", "ward_unit_cost_first_block": 806.42,
         "ward_charges": 806.42, "ward_quantity_unit": "days"}
    ],
    "or_type": None,
    "or_charges": 0,
    "consultation_fee": 100.0,
    "procedure_fee": 200.0,
    "anaesthetist_fee": 50.0,
    "dtf": 333.03,
    "ancillary_charges_llm": 1500.0,
    "estimated_medisave_claimable": 1130.0,
}


class TestHandler:
    @patch("src.lambda_function.inference_jobs_table")
    @patch("src.lambda_function.table")
    @patch("src.lambda_function.s3_client")
    def test_processes_out_file(self, mock_s3, mock_table, mock_jobs_table):
        from src.lambda_function import handler

        body_bytes = json.dumps(SAMPLE_OUTPUT_WARD_DAYS).encode("utf-8")
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=body_bytes))
        }
        mock_table.put_item.return_value = {}
        mock_jobs_table.get_item.return_value = {
            "Item": {"job_id": "job-001", "fa_number": "FA-12345"}
        }

        event = _make_s3_event("my-bucket", "output/job-001.out")
        result = handler(event, None)

        assert result["statusCode"] == 200
        mock_s3.get_object.assert_called_once_with(Bucket="my-bucket", Key="output/job-001.out")
        mock_table.put_item.assert_called_once()

        item = mock_table.put_item.call_args[1]["Item"]
        assert item["job_id"] == "job-001"
        assert item["fa_number"] == "FA-12345"
        assert item["template_id"] == 2
        # Verify Decimal conversion
        assert isinstance(item["consultation_fee"], Decimal)

    @patch("src.lambda_function.inference_jobs_table")
    @patch("src.lambda_function.table")
    @patch("src.lambda_function.s3_client")
    def test_skips_non_out_files(self, mock_s3, mock_table, mock_jobs_table):
        from src.lambda_function import handler

        event = _make_s3_event("my-bucket", "output/job-001.json")
        result = handler(event, None)

        assert result["statusCode"] == 200
        mock_s3.get_object.assert_not_called()
        mock_table.put_item.assert_not_called()

    @patch("src.lambda_function.inference_jobs_table")
    @patch("src.lambda_function.table")
    @patch("src.lambda_function.s3_client")
    def test_idempotency_skips_duplicate(self, mock_s3, mock_table, mock_jobs_table):
        """Duplicate S3 event should not overwrite existing DynamoDB record."""
        from botocore.exceptions import ClientError
        from src.lambda_function import handler

        body_bytes = json.dumps(SAMPLE_OUTPUT_WARD_DAYS).encode("utf-8")
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=body_bytes))
        }
        mock_jobs_table.get_item.return_value = {"Item": {"job_id": "job-dup"}}
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
            "PutItem",
        )

        event = _make_s3_event("my-bucket", "output/job-dup.out")
        # Should not raise — duplicate is silently skipped
        result = handler(event, None)
        assert result["statusCode"] == 200

    @patch("src.lambda_function.inference_jobs_table")
    @patch("src.lambda_function.table")
    @patch("src.lambda_function.s3_client")
    def test_conditional_write_uses_attribute_not_exists(self, mock_s3, mock_table, mock_jobs_table):
        from src.lambda_function import handler

        body_bytes = json.dumps(SAMPLE_OUTPUT_WARD_DAYS).encode("utf-8")
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=body_bytes))
        }
        mock_jobs_table.get_item.return_value = {"Item": {"job_id": "x"}}
        mock_table.put_item.return_value = {}

        handler(_make_s3_event("b", "output/x.out"), None)
        call_kwargs = mock_table.put_item.call_args[1]
        assert call_kwargs["ConditionExpression"] == "attribute_not_exists(job_id)"


class TestPartialBatchResilience:
    @patch("src.lambda_function.inference_jobs_table")
    @patch("src.lambda_function.table")
    @patch("src.lambda_function.s3_client")
    def test_one_failure_still_processes_others(self, mock_s3, mock_table, mock_jobs_table):
        from src.lambda_function import handler

        good_body = json.dumps(SAMPLE_OUTPUT_WARD_DAYS).encode("utf-8")

        def side_effect(Bucket, Key):
            if "bad" in Key:
                raise ValueError("S3 read error")
            return {"Body": MagicMock(read=MagicMock(return_value=good_body))}

        mock_s3.get_object.side_effect = side_effect
        mock_jobs_table.get_item.return_value = {"Item": {}}
        mock_table.put_item.return_value = {}

        event = _make_multi_record_event([
            ("bucket", "output/good.out"),
            ("bucket", "output/bad.out"),
            ("bucket", "output/also-good.out"),
        ])

        with pytest.raises(RuntimeError, match="1 record"):
            handler(event, None)

        # Both good records should still have been written
        assert mock_table.put_item.call_count == 2

    @patch("src.lambda_function.inference_jobs_table")
    @patch("src.lambda_function.table")
    @patch("src.lambda_function.s3_client")
    def test_all_failures_raises_with_count(self, mock_s3, mock_table, mock_jobs_table):
        from src.lambda_function import handler

        mock_s3.get_object.side_effect = Exception("boom")

        event = _make_multi_record_event([
            ("bucket", "output/a.out"),
            ("bucket", "output/b.out"),
        ])

        with pytest.raises(RuntimeError, match="2 record"):
            handler(event, None)


class TestFloatToDecimalConversion:
    @patch("src.lambda_function.inference_jobs_table")
    @patch("src.lambda_function.table")
    @patch("src.lambda_function.s3_client")
    def test_nested_floats_converted(self, mock_s3, mock_table, mock_jobs_table):
        from src.lambda_function import handler

        body_bytes = json.dumps(SAMPLE_OUTPUT_WARD_DAYS).encode("utf-8")
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=body_bytes))
        }
        mock_jobs_table.get_item.return_value = {"Item": {}}
        mock_table.put_item.return_value = {}

        handler(_make_s3_event("b", "output/x.out"), None)
        item = mock_table.put_item.call_args[1]["Item"]

        # Top-level float -> Decimal
        assert isinstance(item["consultation_fee"], Decimal)
        # Nested in accommodation dict
        assert isinstance(item["accommodation_1"]["room_rate"], Decimal)

    @patch("src.lambda_function.inference_jobs_table")
    @patch("src.lambda_function.table")
    @patch("src.lambda_function.s3_client")
    def test_none_values_stripped_from_item(self, mock_s3, mock_table, mock_jobs_table):
        from src.lambda_function import handler

        body_bytes = json.dumps(SAMPLE_OUTPUT_WARD_DAYS).encode("utf-8")
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=body_bytes))
        }
        mock_jobs_table.get_item.return_value = {"Item": {}}
        mock_table.put_item.return_value = {}

        handler(_make_s3_event("b", "output/x.out"), None)
        item = mock_table.put_item.call_args[1]["Item"]

        # accommodation_2 and accommodation_3 are None and should be stripped
        assert "accommodation_2" not in item
        assert "accommodation_3" not in item


class TestMalformedInput:
    @patch("src.lambda_function.inference_jobs_table")
    @patch("src.lambda_function.table")
    @patch("src.lambda_function.s3_client")
    def test_invalid_json_raises(self, mock_s3, mock_table, mock_jobs_table):
        from src.lambda_function import handler

        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"not valid json"))
        }

        event = _make_s3_event("b", "output/bad.out")
        with pytest.raises(RuntimeError):
            handler(event, None)

    @patch("src.lambda_function.inference_jobs_table")
    @patch("src.lambda_function.table")
    @patch("src.lambda_function.s3_client")
    def test_empty_json_object_produces_unclassified(self, mock_s3, mock_table, mock_jobs_table):
        from src.lambda_function import handler

        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=b"{}"))
        }
        mock_jobs_table.get_item.return_value = {"Item": {}}
        mock_table.put_item.return_value = {}

        result = handler(_make_s3_event("b", "output/empty.out"), None)
        assert result["statusCode"] == 200

        item = mock_table.put_item.call_args[1]["Item"]
        assert item["template_id"] == 0
        assert item["template_name"] == "UNCLASSIFIED"

    @patch("src.lambda_function.inference_jobs_table")
    @patch("src.lambda_function.table")
    @patch("src.lambda_function.s3_client")
    def test_empty_records_list(self, mock_s3, mock_table, mock_jobs_table):
        from src.lambda_function import handler

        result = handler({"Records": []}, None)
        assert result["statusCode"] == 200
        mock_s3.get_object.assert_not_called()
