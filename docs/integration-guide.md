# FC Form Processor — ADO Portal Integration Guide

## 1. End-to-End System Flow

```
ADO Portal                SageMaker                  S3                     Lambda                  DynamoDB
   |                         |                        |                       |                       |
   |-- submit job ---------->|                        |                       |                       |
   |   (async invoke)        |                        |                       |                       |
   |                         |-- write .out --------->|                       |                       |
   |                         |   output/{job_id}.out  |                       |                       |
   |                         |                        |-- S3 event --------->|                       |
   |                         |                        |   ObjectCreated       |                       |
   |                         |                        |                       |-- read .out from S3   |
   |                         |                        |                       |-- classify template   |
   |                         |                        |                       |-- map FC fields       |
   |                         |                        |                       |-- put_item --------->|
   |                         |                        |                       |                       |
   |-- poll / query ---------|------------------------|-- (or API GW) --------|-- GetItem(job_id) -->|
   |<-- FC form data --------|------------------------|----------------------|<--------------------- |
   |                         |                        |                       |                       |
   |-- render FC report      |                        |                       |                       |
```

**Timing**: The Lambda typically completes within 1-3 seconds of the `.out` file being written. The frontend can poll DynamoDB by `job_id` after receiving confirmation that the SageMaker job has completed.

---

## 2. Lambda Function: FC Form Processor

### 2.1 Trigger

| Property | Value |
|----------|-------|
| Event source | S3 `ObjectCreated` notification |
| Bucket | `fph-async-inference-{env}-{account_id}` |
| Prefix filter | `output/` |
| Suffix filter | `.out` |
| Runtime | Python 3.12 |
| Memory | 256 MB |
| Timeout | 30 seconds |
| Environment variable | `DYNAMODB_TABLE` (e.g. `FphFCFormData-dev`) |

### 2.2 What It Does

The Lambda (`src/lambda_function.py`) performs these steps for each `.out` file in the S3 event:

1. **Parse the S3 event** to extract `bucket` and `key` (e.g. `output/a1b2c3d4.out`).
2. **Skip non-`.out` files** — only processes files ending in `.out`.
3. **Read and parse** the JSON body from S3. This is the raw SageMaker inference output.
4. **Classify the template** via `determine_template()` — determines which of the 7 FC form layouts applies (see Section 4).
5. **Map fields** via `map_fc_fields()` — transforms raw SageMaker fields into the structured FC form record with computed totals (see Section 3).
6. **Write to DynamoDB** with a conditional expression `attribute_not_exists(job_id)` to ensure idempotency. Duplicate S3 events are silently skipped.
7. **Log structured context** including `job_id`, `bucket`, `key`, and `template_id` for operational tracing.

### 2.3 Error Handling

- **Partial-batch resilience**: If the S3 event contains multiple records, each is processed independently. One failure does not block others. After all records are attempted, a `RuntimeError` is raised listing the failed keys (so Lambda retries them).
- **Idempotency**: The DynamoDB conditional write (`attribute_not_exists(job_id)`) means that if the same `.out` file triggers the Lambda twice (S3 notifications are at-least-once), the second write is a no-op.
- **Unclassified payloads**: If the SageMaker output has no valid ward or OR data, the record is still written with `template_id=0` and `template_name="UNCLASSIFIED"`. The frontend should treat this as requiring manual review.

### 2.4 `job_id` Derivation

The `job_id` is extracted from the S3 key:

```
S3 key:  output/a1b2c3d4-ef56-7890-abcd-1234567890ab.out
job_id:  a1b2c3d4-ef56-7890-abcd-1234567890ab
```

This is the same identifier the ADO portal uses when invoking the SageMaker async endpoint, so the frontend can look up results by the `job_id` it already knows.

---

## 3. DynamoDB Record Schema

### 3.1 Table Configuration

| Property | Value |
|----------|-------|
| Table name | `FphFCFormData-{env}` (e.g. `FphFCFormData-dev`) |
| Partition key | `job_id` (String) |
| Sort key | None |

### 3.2 Full Record Structure

All monetary values are stored as DynamoDB `Number` (Decimal), rounded to 2 decimal places. Fields with `null` values at the top level are omitted from the record entirely.

```jsonc
{
  // === Primary Key ===
  "job_id": "a1b2c3d4-ef56-7890-abcd-1234567890ab",   // String (partition key)

  // === Template Classification ===
  "template_id": 2,                    // Number: 1-7, or 0 for UNCLASSIFIED
  "template_name": "Ward only (days)", // String: human-readable scenario

  // === Section 1: Estimated Doctor's Fees (Excludes GST) ===
  "consultation_fee": 150.00,          // Number
  "procedure_fee": 500.00,             // Number
  "anaesthetist_fee": 200.00,          // Number
  "assistant_surgeon_fee": 0.00,       // Number (manual override, default 0)
  "total_doctors_fees": 850.00,        // Number (sum of above four)

  // === Section 2: Estimated Hospital Charges (Excludes GST) ===

  // -- Accommodation Charges (up to 3 rows) --
  "accommodation_1": {                 // Map (always present for template_id 1-7)
    "room_type": "P5 Private Deluxe",  //   String: display label for the FC form
    "room_rate": 1488.07,              //   Number: unit cost ($ per day/hour/block)
    "quantity": null,                  //   Number | null: subq quantity (null for first blocks)
    "total": 5952.28                   //   Number: line total
  },
  "accommodation_2": {                 // Map | absent: present for templates 1,4,5,7
    "room_type": "Day Surgery Suite (First 3 Hours)",
    "room_rate": 151.38,
    "quantity": null,
    "total": 151.38
  },
  // "accommodation_3" only present for template 1 (Ward+OR) when OR has subq block
  "accommodation_3": {                 // Map | absent
    "room_type": "Day Surgery Suite (Subsequent Hour or Part Thereof)",
    "room_rate": 68.81,
    "quantity": 3,                     //   Number: subsequent hours/units
    "total": 206.43
  },

  // -- Daily Treatment Fees --
  "daily_treatment_fee": 1332.12,      // Number

  // -- Ancillary Charges --
  "ancillary_charges": 4480.00,        // Number

  // -- Hospital Charges Total --
  "total_hospital_charges": 12121.81,  // Number (all accommodation + DTF + ancillary)

  // === Section 3: Total Estimated Charges (Excludes GST) ===
  "total_estimated_amount": 12971.81,  // Number (doctors_fees + hospital_charges)
  "estimated_medisave_claimable": 3310.00, // Number
  "deposit_required": 9661.81,         // Number (total - medisave)

  // === Metadata ===
  "consumables_list": [ ... ],         // List: detailed consumable line items (pass-through)
  "flags": {                           // Map
    "backup_logic": false,             //   Bool: SageMaker used backup estimation logic
    "manual": false,                   //   Bool: requires manual review
    "warning": false,                  //   Bool: SageMaker flagged a warning
    "patched": false                   //   Bool: output was patched post-inference
  },
  "raw_output_s3_key": "output/a1b2c3d4.out", // String: reference to source file
  "processed_at": "2026-02-09T08:30:00+00:00"  // String: ISO 8601 UTC timestamp
}
```

### 3.3 Field Presence by Template

| Field | T1 | T2 | T3 | T4 | T5 | T6 | T7 |
|-------|----|----|----|----|----|----|-----|
| `accommodation_1` | Ward row | Ward row | Ward row | Ward first-block | Ward type 1 | OR first-block | OR first-block |
| `accommodation_2` | OR first-block | absent | absent | Ward subq-block | Ward type 2 | absent | OR subq-block |
| `accommodation_3` | OR subq-block* | absent | absent | absent | absent | absent | absent |

*`accommodation_3` is only present in Template 1 when `or_unit_cost_subq > 0`.

### 3.4 `accommodation_*` Row Display Formatting

Each accommodation object maps to one row in the "Accommodation Charges" section of the FC form PDF:

```
| {room_type}    | $ {room_rate} [x {quantity} Unit(s)] |  $  {total} |
```

Rules:
- If `quantity` is `null`, display as `$ {room_rate}` without the multiplier (first-block pricing).
- If `quantity` is set, display as `$ {room_rate} x {quantity} Hour(s)` or `Day(s)` depending on context.
- The unit label (Day/Hour) is encoded in the `room_type` string itself (e.g. "DAY SUITE BED (4-BED)-PER SUBQ").

---

## 4. Template Scenarios — Frontend Rendering Guide

The `template_id` field tells the frontend which FC form layout to render. Each template corresponds to one of the 7 reference PDFs in `resources/FC Templates/`.

### Template 1: Ward + OR (2 or 3 accommodation rows)

**When**: Patient has both a ward stay and an operating room procedure.

**Accommodation Charges section**:
| Row | Source | Example |
|-----|--------|---------|
| `accommodation_1` | Ward | P5 Private Deluxe — $1,488.07 x 4 Day(s) = $5,952.28 |
| `accommodation_2` | OR first block | Day Surgery Suite (First 3 Hours) — $151.38 |
| `accommodation_3` | OR subsequent block (if exists) | Day Surgery Suite (Subsequent Hour or Part Thereof) — $68.81 x 3 Hour(s) = $206.43 |

**Daily Treatment Fees section**: May have two DTF rows (ward DTF + OR DTF). The `daily_treatment_fee` field is the total.

**Ancillary Charges**: Includes `or_charges` in the total (ward+OR scenario only).

### Template 2: Ward Only — Days (1 accommodation row)

**When**: Patient has a ward stay billed in days, no OR.

| Row | Example |
|-----|---------|
| `accommodation_1` | Private — $806.42 x 1 Day(s) = $806.42 |

### Template 3: Ward Only — Hours, 1 Block (1 accommodation row)

**When**: Patient has a short ward stay billed in hours, single pricing block.

| Row | Example |
|-----|---------|
| `accommodation_1` | DAY SUITE BED (4 BED)-1ST 3HR — $123.85 |

### Template 4: Ward Only — Hours, 2 Blocks (2 accommodation rows)

**When**: Patient has an hours-based ward stay that exceeds the first charging block.

| Row | Example |
|-----|---------|
| `accommodation_1` | DAY SUITE BED (4 BED) — $123.85 |
| `accommodation_2` | DAY SUITE BED (4-BED)-PER SUBQ — $55.05 x 3 Hour(s) = $165.15 |

### Template 5: Ward Only — 2 Types (2 accommodation rows, potentially 2 DTF rows)

**When**: Patient stayed in two different ward types (e.g. transferred from private room to day surgery suite).

| Row | Example |
|-----|---------|
| `accommodation_1` | P5 Private Deluxe — $1,488.07 x 1 Day(s) = $1,488.07 |
| `accommodation_2` | Day Surgery Suite (First 3 Hours) — $151.38 |

**Daily Treatment Fees**: May list two DTF lines (one per ward type). The `daily_treatment_fee` field is the combined total.

### Template 6: OR Only — 1 Block (1 accommodation row)

**When**: Outpatient procedure, OR only, no ward stay.

| Row | Example |
|-----|---------|
| `accommodation_1` | Cardiovascular Suite (First 4 Hours) — $165.14 |

### Template 7: OR Only — 2 Blocks (2 accommodation rows)

**When**: OR procedure that exceeds the first charging block.

| Row | Example |
|-----|---------|
| `accommodation_1` | Cardiovascular Suite (First 4 Hours) — $165.14 |
| `accommodation_2` | Cardiovascular Suite (Subsequent Hour or Part Thereof) — $55.05 x 1 Hour(s) = $55.05 |

### Template 0: UNCLASSIFIED

**When**: The SageMaker output could not be classified (no valid ward or OR data).

**Frontend action**: Display a warning banner and route to manual review. The record will still have computed totals (likely all zeros) and the `flags` metadata.

---

## 5. Frontend Integration — Querying FC Form Data

### 5.1 DynamoDB GetItem (Direct Access)

The simplest integration. The frontend (or its backend-for-frontend) queries DynamoDB directly using the `job_id` that was used when submitting the SageMaker job.

```python
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("FphFCFormData-dev")

response = table.get_item(Key={"job_id": "a1b2c3d4-ef56-7890-abcd-1234567890ab"})
fc_data = response.get("Item")

if fc_data is None:
    # Not yet processed — either still in SageMaker queue or Lambda hasn't run yet
    pass
```

### 5.2 Polling Strategy

Since the Lambda is event-driven (S3 trigger), there is a short delay between SageMaker writing the `.out` file and the FC record appearing in DynamoDB. Recommended polling:

1. After the ADO portal receives confirmation that the SageMaker job completed, wait **2 seconds**.
2. Call `GetItem` with the `job_id`.
3. If the item is not found, retry up to **5 times** with **2-second intervals**.
4. If still not found after 10 seconds, display an error and log for investigation.

### 5.3 API Gateway Integration (Optional)

If the frontend accesses data via an API Gateway, create a thin Lambda or direct DynamoDB integration:

```
GET /fc-report/{job_id}

Response 200:
{
  "job_id": "...",
  "template_id": 2,
  "template_name": "Ward only (days)",
  "consultation_fee": 150.00,
  ...
}

Response 404:
{
  "error": "FC report not found for job_id"
}
```

---

## 6. Rendering the FC Report

### 6.1 Mapping DynamoDB Fields to FC Form Sections

The FC form PDF (FORM-CR-PSC-002 R8-10/24) has these sections, mapped as follows:

#### Admission Details (header — not from this Lambda)

These fields come from the original ADO portal submission, not from this Lambda's output:

| FC Form Field | Source |
|---------------|--------|
| Specialty | ADO portal input |
| Surgical Procedure/Operation | ADO portal input |
| Expected Length of Stay | ADO portal input |
| Patient Name / Patient ID | ADO portal input |
| Admitting Doctor | ADO portal input |
| Admission Date / Time | ADO portal input |
| Room Type | ADO portal input (or derive from `accommodation_1.room_type`) |

#### Estimated Doctor's Fees (Excludes GST)

| FC Form Field | DynamoDB Field |
|---------------|----------------|
| Consultation Fee(s) | `consultation_fee` |
| Procedure / Surgeon Fee(s) | `procedure_fee` |
| Assistant Surgeon Fee(s) | `assistant_surgeon_fee` |
| Anaesthetist Fee(s) | `anaesthetist_fee` |
| **TOTAL ESTIMATED DOCTOR(S)' FEES** | `total_doctors_fees` |

Display "N/A" in the MOH Benchmark column if benchmark data is unavailable.

#### Estimated Hospital Charges (Excludes GST)

**Accommodation Charges**:

Render one row per non-null `accommodation_*` field:

| FC Form Field | DynamoDB Field |
|---------------|----------------|
| Row label | `accommodation_N.room_type` |
| Rate x Quantity | `$ {accommodation_N.room_rate}` + ` x {accommodation_N.quantity} Unit(s)` if quantity is set |
| Line total | `accommodation_N.total` |

**Daily Treatment Fees**:

| FC Form Field | DynamoDB Field |
|---------------|----------------|
| DTF line total | `daily_treatment_fee` |

**Ancillary\* Charges**:

| FC Form Field | DynamoDB Field |
|---------------|----------------|
| Ancillary total | `ancillary_charges` |

**Daily Companion Rate**: Not computed by this Lambda (always $0.00 — set by the ADO portal if applicable).

| FC Form Field | DynamoDB Field |
|---------------|----------------|
| **TOTAL ESTIMATED HOSPITAL CHARGES** | `total_hospital_charges` |

#### Total Estimated Charges (Excludes GST)

| FC Form Field | DynamoDB Field |
|---------------|----------------|
| Total Estimated Doctors' Charges | `total_doctors_fees` |
| Total Estimated Hospital Charges | `total_hospital_charges` |
| **Total Estimated Amount** | `total_estimated_amount` |
| Estimated Medisave Claimable | `estimated_medisave_claimable` |
| **Deposit Required** | `deposit_required` |

### 6.2 Handling the `flags` Object

| Flag | Frontend Behavior |
|------|-------------------|
| `flags.manual` = true | Show a "Manual Review Required" banner. Do not auto-generate the report. |
| `flags.warning` = true | Show a warning indicator next to the estimate. Allow generation but flag for review. |
| `flags.backup_logic` = true | Informational — log or show a subtle note that backup estimation was used. |
| `flags.patched` = true | Informational — the SageMaker output was post-processed/patched before this Lambda ran. |

### 6.3 Handling `template_id = 0` (UNCLASSIFIED)

If the frontend receives `template_id = 0`:
- Do **not** auto-generate the FC report.
- Display: "This estimate could not be automatically classified. Please review manually."
- Provide a link to the raw SageMaker output via `raw_output_s3_key` for manual inspection.

---

## 7. Environment Configuration

The system uses environment variables for cross-environment deployment:

| Variable | Lambda | Description |
|----------|--------|-------------|
| `DYNAMODB_TABLE` | Yes | DynamoDB table name (e.g. `FphFCFormData-dev`, `FphFCFormData-prod`) |
| `AWS_DEFAULT_REGION` | Yes (Lambda runtime) | AWS region for boto3 clients |

The S3 bucket name is **not** configured as an environment variable — it arrives dynamically via the S3 event notification payload.

| Environment | S3 Bucket | DynamoDB Table |
|-------------|-----------|----------------|
| dev | `fph-async-inference-dev-{account_id}` | `FphFCFormData-dev` |
| uat | `fph-async-inference-uat-{account_id}` | `FphFCFormData-uat` |
| prod | `fph-async-inference-prod-{account_id}` | `FphFCFormData-prod` |

---

## 8. IAM Permissions Required

### Lambda Execution Role

```json
{
  "Effect": "Allow",
  "Action": ["s3:GetObject"],
  "Resource": "arn:aws:s3:::fph-async-inference-{env}-{account_id}/output/*"
}
```

```json
{
  "Effect": "Allow",
  "Action": ["dynamodb:PutItem"],
  "Resource": "arn:aws:dynamodb:{region}:{account_id}:table/FphFCFormData-{env}"
}
```

### Frontend / BFF Read Access

```json
{
  "Effect": "Allow",
  "Action": ["dynamodb:GetItem"],
  "Resource": "arn:aws:dynamodb:{region}:{account_id}:table/FphFCFormData-{env}"
}
```

---

## 9. Worked Example

**Input**: SageMaker writes `output/job-xyz-123.out` with:

```json
{
  "ward_breakdown": [
    {
      "ward_type": "P5 Private Deluxe",
      "ward_unit_cost_first_block": 1488.07,
      "ward_charges": 5952.28,
      "ward_quantity_unit": "days"
    }
  ],
  "or_type": null,
  "or_charges": 0,
  "consultation_fee": 0,
  "procedure_fee": 0,
  "anaesthetist_fee": 0,
  "dtf": 1332.12,
  "ancillary_charges_llm": 4480.00,
  "doctor_prescribed_charges": 0,
  "estimated_medisave_claimable": 3310.00,
  "consumables_list": [],
  "backup_logic_flag": false,
  "manual_flag": false,
  "warning_flag": false,
  "patched_flag": false
}
```

**Lambda processing**:

1. Template selector: ward exists (P5 Private Deluxe), no OR → `template_id=2` (Ward only, days)
2. Field mapper produces:
   - `accommodation_1`: P5 Private Deluxe, rate=$1,488.07, total=$5,952.28
   - `accommodation_2`: absent (None, stripped before DynamoDB write)
   - `daily_treatment_fee`: $1,332.12
   - `ancillary_charges`: $4,480.00 (no OR charges added since there's no ward+OR scenario — wait, actually ancillary = llm + prescribed + or_charges when ward exists, but or_charges=0 here, so $4,480.00)
   - `total_hospital_charges`: 5,952.28 + 1,332.12 + 4,480.00 = $11,764.40
   - `total_doctors_fees`: $0.00
   - `total_estimated_amount`: $11,764.40
   - `deposit_required`: 11,764.40 - 3,310.00 = $8,454.40

**DynamoDB record** (key = `job-xyz-123`):

```json
{
  "job_id": "job-xyz-123",
  "template_id": 2,
  "template_name": "Ward only (days)",
  "consultation_fee": 0,
  "procedure_fee": 0,
  "anaesthetist_fee": 0,
  "assistant_surgeon_fee": 0,
  "total_doctors_fees": 0,
  "accommodation_1": {
    "room_type": "P5 Private Deluxe",
    "room_rate": 1488.07,
    "quantity": null,
    "total": 5952.28
  },
  "daily_treatment_fee": 1332.12,
  "ancillary_charges": 4480.00,
  "total_hospital_charges": 11764.40,
  "total_estimated_amount": 11764.40,
  "estimated_medisave_claimable": 3310.00,
  "deposit_required": 8454.40,
  "flags": { "backup_logic": false, "manual": false, "warning": false, "patched": false },
  "raw_output_s3_key": "output/job-xyz-123.out",
  "processed_at": "2026-02-09T08:30:00+00:00"
}
```

**Frontend renders** the FC form matching the Template 2 PDF exactly — single accommodation row, single DTF row, ancillary, and totals summing to $11,764.40 with deposit $8,454.40.
