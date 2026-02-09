# FC Form Processor Lambda - Implementation Plan

## Context

The ADO system uses a SageMaker async endpoint to compute Financial Counselling (FC) estimates. The endpoint writes raw output (`.out` JSON files) to `s3://fph-async-inference-dev-808285222697/output/`. A new Lambda function is needed to:

1. **Trigger** on S3 `ObjectCreated` events in the `output/` prefix
2. **Read** the raw SageMaker JSON output from S3
3. **Map** raw fields to FC form fields using the conditional logic defined in the FC sheet
4. **Determine** which of the 7 FC template scenarios applies
5. **Compute** derived totals (doctor's fees, hospital charges, total estimate, deposit)
6. **Store** the processed FC form data in a new DynamoDB table

> **Comment:** Replace hardcoded environment names (bucket/table) with environment variables so the same code deploys cleanly across `dev`, `uat`, and `prod`.

## Key Data Mapping

### SageMaker Output Structure (from S3 `.out` files)
- `ward_breakdown`: list of `{ward_type, length_of_stay, ward_charges, ward_dtf_total, ward_total, ward_unit_cost_first_block, ward_quantity_unit}`
- `ward_unit_cost_subq`, `ward_charging_block`
- `or_type`, `or_charges`, `or_dtf`, `or_total`, `or_unit_cost_first_block`, `or_charging_block_hours`, `or_unit_cost_subq`
- `consultation_fee`, `procedure_fee`, `anaesthetist_fee`, `doctor_prescribed_charges`
- `ancillary_charges_llm`, `dtf` (daily treatment fee)
- `estimated_medisave_claimable`
- `consumables_list`: detailed line items
- `ward_stays`: original input ward stays
- Flags: `backup_logic_flag`, `manual_flag`, `warning_flag`, `patched_flag`

> **Comment:** Add an explicit schema contract here: required vs optional fields, default values, and nullability for each field.
> **Comment:** For money values, specify numeric type and conversion strategy (recommended: parse to `Decimal`, reject/normalize invalid numeric strings).

### FC Form Field Mapping (from FC sheet, rows 14-24)

**Accommodation 1** (ward-first, OR-fallback):
- Room type first block: `ward_breakdown[0].ward_type` if exists, else `or_type`
- Room type subq: conditional on ward vs OR, append "-PER SUBQ" if subq cost > 0
- Room rate first block: `ward_breakdown[0].ward_unit_cost_first_block` if exists, else `or_unit_cost_first_block`
- Room rate subq: `ward_unit_cost_subq` or `or_unit_cost_subq` with same conditional
- Quantity: `ward_quantity_subq_1` or `or_quantity_subq_1`

**Accommodation 2** (second ward type, or OR-fallback):
- Same logic but using `ward_breakdown[1]` / second OR block

**Ancillary charges**:
- If ward exists: `ancillary_charges_llm + doctor_prescribed_charges + or_charges`
- If ward is None (OR-only): `ancillary_charges_llm + doctor_prescribed_charges`

**DTF**: `dtf` from output (ward_dtf_total already in ward_breakdown)

**Totals**:
- Doctor's fees = consultation + procedure + anaesthetist + (manual assistant surgeon fee, default 0)
- Hospital charges = sum of accommodation rows + DTF + ancillary
- Total = doctor's fees + hospital charges
- Deposit = total - medisave claimable

> **Comment:** Define rounding policy explicitly (e.g., round half up to 2 d.p.) and when rounding occurs (per-field vs final total only) to avoid reconciliation drift.

### 7 Template Scenarios

| # | Scenario | Condition | Template |
|---|----------|-----------|----------|
| 1 | Ward + OR | ward_breakdown non-empty AND or_type not None/0 charges | Ward: Accommodation+DTF, OR: Ancillary |
| 2 | Ward only (days) | ward exists, no OR, quantity_unit="days" | Accommodation+DTF only |
| 3 | Ward only (hours, 1 block) | ward exists, no OR, quantity_unit="hours", 1 ward entry | Accommodation+DTF only |
| 4 | Ward only (hours, 2 blocks) | ward exists, no OR, quantity_unit="hours", 2 ward entries | Accommodation+DTF only |
| 5 | Ward only (2 types) | 2+ ward entries with different ward_types | Accommodation+DTF only |
| 6 | OR only (1 block) | no ward, OR exists, 1 block | OR: Accommodation+DTF |
| 7 | OR only (2 blocks) | no ward, OR exists, 2+ blocks | OR: Accommodation+DTF |

> **Comment:** Add deterministic precedence rules for overlapping conditions (especially scenarios 3/4/5) so classification is unambiguous.
> **Comment:** Add an explicit fallback behavior for unclassified/malformed payloads (e.g., `template_id=0`, `template_name="UNCLASSIFIED"`, `warning_flag=true`).

## Project Structure

```
fph-fc-report/
├── src/
│   ├── lambda_function.py           # Lambda handler (S3 event trigger)
│   ├── fc_field_mapper.py           # Field mapping logic
│   └── fc_template_selector.py      # Template scenario determination
├── tests/
│   ├── test_fc_field_mapper.py
│   ├── test_fc_template_selector.py
│   └── test_lambda_function.py
├── resources/                        # Existing reference docs
│   ├── ADO System Flow (1).xlsx
│   └── FC Templates/
├── requirements.txt
├── plan.md
└── CLAUDE.md
```

## Implementation Steps

### Step 1: Create `src/fc_template_selector.py`
- Function `determine_template(sagemaker_output: dict) -> dict` that returns:
  - `template_id` (1-7)
  - `template_name` (human-readable)
  - `has_ward` (bool)
  - `has_or` (bool)
  - `ward_count` (int)
  - `ward_unit` ("days" or "hours" or None)
- Logic:
  - Check if ward data exists: `ward_breakdown` non-empty with valid ward_type
  - Check if OR data exists: `or_type` is not None and `or_charges > 0` or `or_unit_cost_first_block > 0`
  - Apply template rules from the table above

> **Comment:** Clarify operator precedence in OR detection with explicit parentheses. Recommended rule:
> `has_or = (or_type is not None) and ((or_charges > 0) or (or_unit_cost_first_block > 0))`
> **Comment:** Include normalization before checks (trim strings, convert numeric fields safely, treat empty strings as null).

### Step 2: Create `src/fc_field_mapper.py`
- Function `map_fc_fields(sagemaker_output: dict, template_info: dict) -> dict` that produces the FC form data structure:

```python
{
    "job_id": str,                    # extracted from S3 key
    "template_id": int,              # 1-7
    "template_name": str,

    # Doctor's Fees (Section 1)
    "consultation_fee": float,
    "procedure_fee": float,
    "anaesthetist_fee": float,
    "assistant_surgeon_fee": 0.0,     # manual field, default 0
    "total_doctors_fees": float,

    # Hospital Charges - Accommodation 1 (Section 2)
    "accommodation_1": {
        "room_type": str | None,
        "room_rate": float | None,
        "quantity": float | None,
        "total": float | None,
    },

    # Hospital Charges - Accommodation 2 (Section 2, optional)
    "accommodation_2": { ... } | None,

    # Hospital Charges - Accommodation 3 (Template 1 Ward+OR with subq only)
    "accommodation_3": { ... } | None,

    # Hospital Charges - Other
    "daily_treatment_fee": float,
    "ancillary_charges": float,
    "total_hospital_charges": float,

    # Totals
    "total_estimated_amount": float,
    "estimated_medisave_claimable": float,
    "deposit_required": float,

    # Metadata
    "consumables_list": list,          # pass-through for detailed view
    "flags": {
        "backup_logic": bool,
        "manual": bool,
        "warning": bool,
        "patched": bool,
    },
    "raw_output_s3_key": str,          # reference to original .out file
    "processed_at": str,               # ISO timestamp
}
```

- Implement the conditional logic from the FC sheet for each accommodation slot
- Compute totals

> **Comment:** Add behavior for missing accommodation fields (`None` vs `0`) and ensure this is consistent with frontend rendering requirements.
> **Comment:** Include a source-of-truth trace map for auditability (e.g., `field_sources`) so each derived field can be traced to input fields/rules.

### Step 3: Create `src/lambda_function.py`
- S3 event handler:
  1. Parse S3 event to get bucket/key
  2. Skip non-`.out` files
  3. Read and parse the JSON from S3
  4. Call `determine_template()`
  5. Call `map_fc_fields()`
  6. Write result to DynamoDB table `FphFCFormData-dev`
  7. Log success/failure

- DynamoDB table key: `job_id` (extracted from S3 key `output/{job_id}.out`)
  - This allows lookup by job_id from the frontend

> **Comment:** S3 notifications are at-least-once; add idempotency protection (conditional write or versioning) to prevent duplicate processing.
> **Comment:** Add partial-batch resilience: process each record independently and continue when one record fails.
> **Comment:** Use structured logs including `job_id`, `bucket`, `key`, `template_id`, and error category for faster incident triage.

### Step 4: Write tests
- Unit tests for template selection (all 7 scenarios + edge cases)
- Unit tests for field mapping (ward-only, OR-only, mixed, empty data)
- Integration test for Lambda handler with mocked S3/DynamoDB (using moto)

> **Comment:** Add golden-file tests using real `.out` fixtures from historical samples to lock down mapping behavior over time.
> **Comment:** Add negative tests: malformed JSON, missing keys, unexpected data types, negative amounts, empty arrays, and unknown ward/OR types.
> **Comment:** Add contract tests to verify output schema compatibility for downstream/frontend consumers.

### Step 5: Deploy configuration
- Create DynamoDB table `FphFCFormData-dev` with key `job_id` (String)
- Add S3 event notification on `fph-async-inference-dev-808285222697` for `output/` prefix, `.out` suffix
- Lambda config: Python 3.12, 256MB, 30s timeout
- IAM permissions: S3 read on the async inference bucket, DynamoDB write on the new table

> **Comment:** Add failure handling: Lambda DLQ or on-failure destination, retry policy, and CloudWatch alarms for error rate and throttling.
> **Comment:** Reassess DynamoDB schema against access patterns; add GSIs if reads are needed by status/flags/time in addition to `job_id`.
> **Comment:** Define data retention/TTL policy for processed records if long-term persistence is not required.

## Verification
1. Run unit tests locally: `python -m pytest tests/`
2. Deploy Lambda and create DynamoDB table
3. Upload a sample `.out` file to S3 `output/` prefix
4. Check CloudWatch logs for successful processing
5. Query DynamoDB `FphFCFormData-dev` for the processed FC form data
6. Verify template selection matches expected scenario for known test cases
7. Verify field values match the FC sheet mapping logic

> **Comment:** Add measurable acceptance criteria: p95 processing latency, success/error counts, duplicate-event behavior, and alarm validation.
> **Comment:** Add reconciliation checks against FC sheet outputs for a fixed regression dataset before production rollout.
