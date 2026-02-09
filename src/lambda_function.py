"""
FC Form Processor Lambda

Triggered by S3 ObjectCreated events on the async inference output bucket.
Reads raw SageMaker .out JSON, maps fields to FC form structure, and stores
the processed data in DynamoDB.

Idempotency: uses DynamoDB conditional writes (attribute_not_exists) so
duplicate S3 notifications do not overwrite existing records.

Resilience: each S3 record is processed independently; failures are collected
and raised at the end so one bad record does not block the rest.
"""

import json
import logging
import os
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

from src.fc_field_mapper import map_fc_fields
from src.fc_template_selector import determine_template

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "FphFCFormData-dev")
INFERENCE_JOBS_TABLE = os.environ.get("INFERENCE_JOBS_TABLE", "FphInferenceJobs-dev")

s3_client = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMODB_TABLE)
inference_jobs_table = dynamodb.Table(INFERENCE_JOBS_TABLE)


def _convert_floats_to_decimal(obj):
    """Recursively convert float values to Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _convert_floats_to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_floats_to_decimal(i) for i in obj]
    return obj


def _process_record(bucket: str, key: str) -> None:
    """Process a single S3 .out file into DynamoDB."""
    job_id = key.rsplit("/", 1)[-1].replace(".out", "")
    log_ctx = {"job_id": job_id, "bucket": bucket, "key": key}

    logger.info("Reading S3 object", extra=log_ctx)
    response = s3_client.get_object(Bucket=bucket, Key=key)
    raw_body = response["Body"].read().decode("utf-8")
    sagemaker_output = json.loads(raw_body)

    # Look up fa_number from the inference jobs table
    fa_number = None
    try:
        jobs_response = inference_jobs_table.get_item(Key={"job_id": job_id})
        fa_number = jobs_response.get("Item", {}).get("fa_number")
        log_ctx["fa_number"] = fa_number
        logger.info("Looked up fa_number=%(fa_number)s for job_id=%(job_id)s", log_ctx)
    except ClientError:
        logger.warning("Failed to look up fa_number for job_id=%(job_id)s", log_ctx, exc_info=True)

    template_info = determine_template(sagemaker_output)
    log_ctx["template_id"] = template_info["template_id"]
    log_ctx["template_name"] = template_info["template_name"]
    logger.info("Template determined: %(template_name)s (id=%(template_id)d)", log_ctx)

    fc_form_data = map_fc_fields(sagemaker_output, template_info, s3_key=key, fa_number=fa_number)

    item = _convert_floats_to_decimal(fc_form_data)
    # Remove None values (DynamoDB doesn't accept None for top-level attributes)
    item = {k: v for k, v in item.items() if v is not None}

    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(job_id)",
        )
        logger.info("Stored FC form data for job_id=%(job_id)s", log_ctx)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.info(
                "Duplicate event — record already exists for job_id=%(job_id)s, skipping",
                log_ctx,
            )
        else:
            raise


def handler(event, context):
    """Lambda handler for S3 ObjectCreated events.

    Processes .out files from SageMaker async inference output.
    Each record is processed independently for partial-batch resilience.
    """
    errors = []

    for record in event.get("Records", []):
        s3_info = record.get("s3", {})
        bucket = s3_info.get("bucket", {}).get("name", "")
        key = s3_info.get("object", {}).get("key", "")

        if not key.endswith(".out"):
            logger.info("Skipping non-.out file: %s", key)
            continue

        try:
            _process_record(bucket, key)
        except Exception as exc:
            logger.exception(
                "Failed to process s3://%s/%s: %s", bucket, key, exc,
            )
            errors.append({"key": key, "error": str(exc)})

    if errors:
        raise RuntimeError(
            f"Failed to process {len(errors)} record(s): "
            + ", ".join(e["key"] for e in errors)
        )

    return {"statusCode": 200, "body": "Processed successfully"}
