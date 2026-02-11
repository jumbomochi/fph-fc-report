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
   |                         |                        |                       |-- lookup fa_number -->|
   |                         |                        |                       |   (FphInferenceJobs)  |
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
| Environment variables | `DYNAMODB_TABLE` (e.g. `FphFCFormData-dev`), `INFERENCE_JOBS_TABLE` (e.g. `FphInferenceJobs-dev`) |

### 2.2 What It Does

The Lambda (`src/lambda_function.py`) performs these steps for each `.out` file in the S3 event:

1. **Parse the S3 event** to extract `bucket` and `key` (e.g. `output/a1b2c3d4.out`).
2. **Skip non-`.out` files** — only processes files ending in `.out`.
3. **Read and parse** the JSON body from S3. This is the raw SageMaker inference output.
4. **Look up `fa_number`** from `FphInferenceJobs-{env}` using the `job_id`. If the lookup fails (table unreachable or record missing), `fa_number` defaults to `null` and processing continues.
5. **Classify the template** via `determine_template()` — determines which of the 7 FC form layouts applies (see Section 4).
6. **Map fields** via `map_fc_fields()` — transforms raw SageMaker fields into the structured FC form record with computed totals, including `fa_number` (see Section 3).
7. **Write to DynamoDB** with a conditional expression `attribute_not_exists(job_id)` to ensure idempotency. Duplicate S3 events are silently skipped.
8. **Log structured context** including `job_id`, `fa_number`, `bucket`, `key`, and `template_id` for operational tracing.

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
| GSI | `fa_number-index` — partition key: `fa_number` (String), projection: ALL |

### 3.2 Full Record Structure — Render-Ready Output

The output is **render-ready**: the frontend renders the report directly with zero processing logic. All monetary values are pre-formatted strings (comma-separated, 2 decimal places). Rate descriptions are pre-built strings matching the FC form PDF layout. Accommodation and DTF are lists of row dicts that the frontend iterates and renders.

```jsonc
{
  // === Primary Key ===
  "job_id": "a1b2c3d4-ef56-7890-abcd-1234567890ab",   // String (partition key)

  // === Patient Identifier ===
  "fa_number": "FA-12345",              // String | absent: looked up from FphInferenceJobs table
                                        // Queryable via GSI "fa_number-index"

  // === Template Classification ===
  "template_id": 2,                    // Number: 1-7, or 0 for UNCLASSIFIED
  "template_name": "Ward only (days)", // String: human-readable scenario

  // === Section 1: Estimated Doctor's Fees (Excludes GST) ===
  "doctors_fees": {
    "rows": [                          // List: one entry per fee type (always 4)
      {"label": "Consultation Fee(s)", "amount": "150.00"},
      {"label": "Procedure / Surgeon Fee(s)", "amount": "500.00"},
      {"label": "Assistant Surgeon Fee(s)", "amount": "0.00"},
      {"label": "Anaesthetist Fee(s)", "amount": "200.00"}
    ],
    "total": "850.00",                 // String: sum of all fee amounts
    "moh_benchmark": "N/A"             // String: MOH benchmark column value
  },

  // === Section 2: Estimated Hospital Charges (Excludes GST) ===
  "hospital_charges": {
    // -- Accommodation Charges (1-3 rows depending on template) --
    "accommodation_rows": [            // List of render-ready rows
      {
        "label": "P5 Private Deluxe",                // String: row label
        "description": "$ 1,488.07 x 4 Day(s)",      // String: rate description
        "amount": "5,952.28"                          // String: line total
      },
      {
        "label": "Day Surgery Suite (First 3 Hours)",
        "description": "$ 151.38",
        "amount": "151.38"
      }
    ],

    // -- Daily Treatment Fees (1-2 rows depending on template) --
    "dtf_rows": [                      // List of render-ready rows
      {
        "label": "P5 Private Deluxe",                // String: ward_type or "TREATMENT FEE-DAY SUITE"
        "description": "$ 333.03 x 4 Day(s)",        // String: rate description
        "amount": "1,332.12"                          // String: DTF total for this row
      }
    ],

    // -- Other charges --
    "ancillary_charges": "4,480.00",   // String: formatted ancillary total
    "daily_companion_rate": "0.00",    // String: always "0.00" (set by ADO portal if needed)
    "total": "11,764.40"              // String: total hospital charges
  },

  // === Section 3: Total Estimated Charges (Excludes GST) ===
  "totals": {
    "total_doctors_charges": "850.00",         // String
    "total_doctors_charges_moh": "N/A",        // String: MOH benchmark
    "total_hospital_charges": "11,764.40",     // String
    "total_estimated_amount": "12,614.40",     // String: doctors + hospital
    "estimated_medisave_claimable": "3,310.00", // String
    "deposit_required": "9,304.40"             // String: total - medisave
  },

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

### 3.3 Row Counts by Template

| Template | Accommodation Rows | DTF Rows |
|----------|-------------------|----------|
| 1 (Ward+OR) | 2-3 (ward + OR first + OR subq if exists) | 1-2 (ward DTF + OR DTF if exists) |
| 2 (Ward days) | 1 | 1 |
| 3 (Ward hours 1-block) | 1 | 1 |
| 4 (Ward hours 2-blocks) | 2 | 1 |
| 5 (Ward 2 types) | 2 | 1-2 |
| 6 (OR 1-block) | 1 | 1 |
| 7 (OR 2-blocks) | 2 | 1 |
### 3.4 Rendering the Report

The output is **render-ready**. The frontend simply:

1. Iterate `doctors_fees.rows` → render each `{label, amount}` as a row
2. Render `doctors_fees.total` and `doctors_fees.moh_benchmark`
3. Iterate `hospital_charges.accommodation_rows` → render each `{label, description, amount}`
4. Iterate `hospital_charges.dtf_rows` → render each `{label, description, amount}`
5. Render `hospital_charges.ancillary_charges` and `hospital_charges.daily_companion_rate`
6. Render `hospital_charges.total`
7. Render all fields in `totals`

No template-specific conditional logic is needed — the Lambda resolves all row variations. The `template_id` field is informational only.

### 3.5 Rate Description Formats

The `description` field in accommodation and DTF rows uses these patterns:

| Scenario | Format | Example |
|----------|--------|---------|
| Days-based ward | `$ {rate} x {days} Day(s)` | `$ 1,488.07 x 4 Day(s)` |
| Hours-based first block | `$ {rate}` | `$ 123.85` |
| OR first block | `$ {rate}` | `$ 165.14` |
| Subsequent block (ward/OR) | `$ {rate} x {qty} Hour(s)` | `$ 55.05 x 3 Hour(s)` |

---

## 4. Template Scenarios (Reference)

The `template_id` field indicates which FC form scenario was classified. Since the output is render-ready, the frontend does **not** need template-specific rendering logic. This section is for reference only.

| Row | Example |
|-----|---------|
| `accommodation_1` | Cardiovascular Suite (First 4 Hours) — $165.14 |
| `accommodation_2` | Cardiovascular Suite (Subsequent Hour or Part Thereof) — $55.05 x 1 Hour(s) = $55.05 |

### Template 0: UNCLASSIFIED

**When**: The SageMaker output could not be classified (no valid ward or OR data).

**Frontend action**: Display a warning banner and route to manual review. The record will still have computed totals (likely all zeros) and the `flags` metadata.

---

## 5. Frontend Integration — Querying FC Form Data

### 5.1 DynamoDB GetItem (Direct Access by job_id)

The simplest integration. The frontend (or its backend-for-frontend) queries DynamoDB directly using the `job_id` that was used when submitting the SageMaker job.

```python
import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("FphFCFormData-dev")

response = table.get_item(Key={"job_id": "a1b2c3d4-ef56-7890-abcd-1234567890ab"})
fc_data = response.get("Item")

if fc_data is None:
    # Not yet processed — either still in SageMaker queue or Lambda hasn't run yet
    pass
```

### 5.2 DynamoDB Query by fa_number (GSI)

To look up all FC reports for a given patient/case, query the `fa_number-index` GSI:

```python
from boto3.dynamodb.conditions import Key

response = table.query(
    IndexName="fa_number-index",
    KeyConditionExpression=Key("fa_number").eq("FA-12345"),
)
fc_records = response.get("Items", [])
```

This returns all FC form records associated with a given `fa_number`, which is useful when the frontend needs to list historical estimates for a patient.

### 5.3 Polling Strategy

Since the Lambda is event-driven (S3 trigger), there is a short delay between SageMaker writing the `.out` file and the FC record appearing in DynamoDB. Recommended polling:

1. After the ADO portal receives confirmation that the SageMaker job completed, wait **2 seconds**.
2. Call `GetItem` with the `job_id`.
3. If the item is not found, retry up to **5 times** with **2-second intervals**.
4. If still not found after 10 seconds, display an error and log for investigation.

### 5.4 API Gateway Integration (Optional)

If the frontend accesses data via an API Gateway, create a thin Lambda or direct DynamoDB integration:

```
GET /fc-report/{job_id}

Response 200:
{
  "job_id": "...",
  "template_id": 2,
  "template_name": "Ward only (days)",
  "doctors_fees": { "rows": [...], "total": "350.00", ... },
  "hospital_charges": { "accommodation_rows": [...], ... },
  "totals": { ... },
  ...
}

Response 404:
{
  "error": "FC report not found for job_id"
}
```

---

## 6. Rendering the FC Report

### 6.1 Render-Ready Output

The DynamoDB record is **render-ready**. The frontend does **not** need any processing logic — it simply iterates the pre-built rows and displays the pre-formatted values. See Section 3.4 for the rendering flow.

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
| Room Type | ADO portal input (or derive from first `accommodation_rows[0].label`) |

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
| `DYNAMODB_TABLE` | Yes | FC form data table name (e.g. `FphFCFormData-dev`, `FphFCFormData-prod`) |
| `INFERENCE_JOBS_TABLE` | Yes | Inference jobs table for `fa_number` lookup (e.g. `FphInferenceJobs-dev`) |
| `AWS_DEFAULT_REGION` | Yes (Lambda runtime) | AWS region for boto3 clients |

The S3 bucket name is **not** configured as an environment variable — it arrives dynamically via the S3 event notification payload.

| Environment | S3 Bucket | FC Form Table | Inference Jobs Table |
|-------------|-----------|---------------|----------------------|
| dev | `fph-async-inference-dev-{account_id}` | `FphFCFormData-dev` | `FphInferenceJobs-dev` |
| uat | `fph-async-inference-uat-{account_id}` | `FphFCFormData-uat` | `FphInferenceJobs-uat` |
| prod | `fph-async-inference-prod-{account_id}` | `FphFCFormData-prod` | `FphInferenceJobs-prod` |

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

```json
{
  "Effect": "Allow",
  "Action": ["dynamodb:GetItem"],
  "Resource": "arn:aws:dynamodb:{region}:{account_id}:table/FphInferenceJobs-{env}"
}
```

### Frontend / BFF Read Access

```json
{
  "Effect": "Allow",
  "Action": ["dynamodb:GetItem", "dynamodb:Query"],
  "Resource": [
    "arn:aws:dynamodb:{region}:{account_id}:table/FphFCFormData-{env}",
    "arn:aws:dynamodb:{region}:{account_id}:table/FphFCFormData-{env}/index/fa_number-index"
  ]
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
      "ward_quantity_unit": "days",
      "length_of_stay": 4,
      "ward_dtf_total": 1332.12
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

1. Looks up `fa_number` from `FphInferenceJobs-dev` using `job_id=job-xyz-123` → returns `FA-67890`
2. Template selector: ward exists (P5 Private Deluxe), no OR → `template_id=2` (Ward only, days)
3. Field mapper produces render-ready output:
   - 1 accommodation row: P5 Private Deluxe, description "$ 1,488.07 x 4 Day(s)", amount "5,952.28"
   - 1 DTF row: P5 Private Deluxe, description "$ 333.03 x 4 Day(s)", amount "1,332.12"
   - ancillary_charges: "4,480.00"
   - total_hospital_charges: "11,764.40" (5,952.28 + 1,332.12 + 4,480.00)
   - deposit_required: "8,454.40" (11,764.40 - 3,310.00)

**DynamoDB record** (key = `job-xyz-123`):

```json
{
  "job_id": "job-xyz-123",
  "fa_number": "FA-67890",
  "template_id": 2,
  "template_name": "Ward only (days)",
  "doctors_fees": {
    "rows": [
      {"label": "Consultation Fee(s)", "amount": "0.00"},
      {"label": "Procedure / Surgeon Fee(s)", "amount": "0.00"},
      {"label": "Assistant Surgeon Fee(s)", "amount": "0.00"},
      {"label": "Anaesthetist Fee(s)", "amount": "0.00"}
    ],
    "total": "0.00",
    "moh_benchmark": "N/A"
  },
  "hospital_charges": {
    "accommodation_rows": [
      {"label": "P5 Private Deluxe", "description": "$ 1,488.07 x 4 Day(s)", "amount": "5,952.28"}
    ],
    "dtf_rows": [
      {"label": "P5 Private Deluxe", "description": "$ 333.03 x 4 Day(s)", "amount": "1,332.12"}
    ],
    "ancillary_charges": "4,480.00",
    "daily_companion_rate": "0.00",
    "total": "11,764.40"
  },
  "totals": {
    "total_doctors_charges": "0.00",
    "total_doctors_charges_moh": "N/A",
    "total_hospital_charges": "11,764.40",
    "total_estimated_amount": "11,764.40",
    "estimated_medisave_claimable": "3,310.00",
    "deposit_required": "8,454.40"
  },
  "flags": { "backup_logic": false, "manual": false, "warning": false, "patched": false },
  "raw_output_s3_key": "output/job-xyz-123.out",
  "processed_at": "2026-02-09T08:30:00+00:00"
}
```

**Frontend renders** the FC form by iterating the pre-built rows — single accommodation row, single DTF row, ancillary, and totals summing to $11,764.40 with deposit $8,454.40. No processing logic needed.
