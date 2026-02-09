# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FC Form Processor Lambda for Farrer Park Hospital's ADO (Admission, Discharge, Operations) system. Processes raw SageMaker async inference output (`.out` JSON files from S3) into structured Financial Counselling (FC) form data stored in DynamoDB.

## Commands

```bash
# Run all tests
python -m pytest tests/

# Run a single test file
python -m pytest tests/test_fc_template_selector.py

# Run a specific test
python -m pytest tests/test_fc_template_selector.py::TestTemplate1WardAndOR::test_basic

# Install dependencies
pip install -r requirements.txt
```

## Architecture

**Pipeline flow:** S3 `.out` file -> Lambda handler -> template selector -> field mapper -> DynamoDB

- `src/lambda_function.py` — Lambda entry point (`handler`). Triggered by S3 `ObjectCreated` events. Reads `.out` JSON from S3, orchestrates processing, writes to DynamoDB. Uses conditional writes (`attribute_not_exists`) for idempotency. Processes each S3 record independently for partial-batch resilience.
- `src/fc_template_selector.py` — `determine_template()` classifies SageMaker output into one of 7 FC template scenarios based on ward/OR presence, ward quantity units, and block counts. Input values are normalized (strings trimmed, numerics safely converted). Returns template metadata dict used by the field mapper.
- `src/fc_field_mapper.py` — `map_fc_fields()` applies conditional logic to map raw SageMaker fields to the FC form structure: doctor's fees, accommodation slots (up to 3), DTF, ancillary charges, and computed totals. All monetary values rounded to 2 d.p. Template ID determines which accommodation-building path is taken.

## 7 Template Scenarios

Classification precedence for ward-only: 2-types (5) > hours-2-blocks (4) > hours-1-block (3) > days (2). Unclassified payloads get `template_id=0`, `template_name="UNCLASSIFIED"`.

| ID | Scenario | Key Condition |
|----|----------|---------------|
| 1 | Ward + OR | ward_breakdown non-empty AND or_type with charges |
| 2 | Ward only (days) | ward exists, no OR, quantity_unit="days" (default) |
| 3 | Ward only (hours, 1 block) | ward exists, no OR, quantity_unit="hours", 1 entry |
| 4 | Ward only (hours, 2 blocks) | ward exists, no OR, quantity_unit="hours", 2+ entries |
| 5 | Ward only (2 types) | 2+ ward entries with different ward_types |
| 6 | OR only (1 block) | no ward, OR exists, or_unit_cost_subq=0 |
| 7 | OR only (2 blocks) | no ward, OR exists, or_unit_cost_subq>0 |

## Key Design Decisions

- **Idempotency**: DynamoDB conditional writes prevent duplicate S3 notifications from overwriting records.
- **Ancillary charges** differ by scenario: when ward exists, includes `or_charges`; OR-only does not.
- **Accommodation slots**: ward-first with OR-fallback. Template 1 (Ward+OR) can produce 3 accommodation slots.
- **Rounding**: all monetary values `round(..., 2)` at the per-field level (Python built-in rounding).
- **Deposit** = total_estimated_amount - estimated_medisave_claimable.
- Floats are converted to `Decimal` before DynamoDB writes; `None` top-level values are stripped.
- `job_id` is extracted from the S3 key: `output/{job_id}.out`.
- DynamoDB table name and AWS config come from environment variables (`DYNAMODB_TABLE`).

## Reference Materials

- `resources/FC Templates/` — 7 PDF examples of the Farrer Park Hospital Financial Counselling Form, one per template scenario.
- `resources/ADO System Flow (1).xlsx` — System flow reference for the ADO pipeline.
- `plan.md` — Implementation plan with review comments.

## AWS Resources

- **S3 bucket:** `fph-async-inference-dev-*` (trigger prefix: `output/`, suffix: `.out`)
- **DynamoDB table:** `FphFCFormData-dev` (key: `job_id`)
- **Lambda runtime:** Python 3.12, 256MB, 30s timeout
