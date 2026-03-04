# Data Model: Pipeline Configuration

**Feature**: 002-pipeline-config  
**Date**: 2026-03-04

## Entity Changes

### 1. Email Document (Cosmos DB `emails` container)

**Container**: `emails`  
**Partition key**: `/status`  
**Change type**: Non-breaking extension (two new optional fields)

#### Field: `pipelineMode` (new, optional)

| Field | Type | Required | Values | Description |
|---|---|---|---|---|
| `pipelineMode` | `string` | No | `"full"` \| `"triage-only"` | The pipeline mode active when this email was processed |

**Notes**:
- Written during `update_email_classification()` when `step="final"`.
- Existing documents without this field are assumed to have been processed in `"full"` mode.
- Used by the dashboard to display whether classification was skipped.

#### Field: `stepsExecuted` (new, optional)

| Field | Type | Required | Values | Description |
|---|---|---|---|---|
| `stepsExecuted` | `string[]` | No | Ordered list of step names | Processing steps completed for this email |

**Allowed step values**:
| Step | Description | Present in `full` | Present in `triage-only` |
|---|---|---|---|
| `"triage"` | Relevance check (PE or not) | Yes | Yes |
| `"pre-processing"` | Attachment download, extraction, OCR | Yes | Yes |
| `"classification"` | PE event type classification via LLM | Yes | No |
| `"routing"` | Sent to output queue | Yes | Yes |

**Full-mode example**: `["triage", "pre-processing", "classification", "routing"]`  
**Triage-only example**: `["triage", "pre-processing", "routing"]`

#### Updated field interactions

| Field | Impact |
|---|---|
| `status` | In triage-only mode, a relevant email gets status `"triaged"`. In full mode, status remains `"classified"`. **Decision**: Use `"triaged"` for triage-only, `"classified"` for full mode. |
| `queue` | New possible value: the triage-complete queue name (configurable). In full mode, values remain `"discarded"`, `"archival-pending"`, `"human-review"`. |
| `classification` | In triage-only mode, this field is `null` or absent (classification was skipped). |

#### Full email document example (triage-only mode)

```json
{
  "id": "AAMkADI5...",
  "status": "triaged",
  "confidenceLevel": "pending",
  "receivedAt": "2026-03-04T14:00:00Z",
  "from": "sender@example.com",
  "subject": "PE Documents - Capital Call",
  "hasAttachments": true,
  "attachmentsCount": 1,
  "emailBody": "<html>...</html>",
  "attachmentPaths": [
    {"path": "AAMkADI5.../invoice.pdf", "source": "attachment"}
  ],
  "relevanceCheck": {
    "isRelevant": true,
    "confidence": 0.92,
    "reasoning": "Subject mentions PE documents and capital call..."
  },
  "classification": null,
  "pipelineMode": "triage-only",
  "stepsExecuted": ["triage", "pre-processing", "routing"],
  "queue": "triage-complete",
  "processedAt": "2026-03-04T14:00:05Z",
  "updatedAt": "2026-03-04T14:00:05Z"
}
```

#### Full email document example (full-pipeline mode)

```json
{
  "id": "AAMkADI5...",
  "status": "classified",
  "confidenceLevel": "high",
  "receivedAt": "2026-03-04T14:00:00Z",
  "from": "sender@example.com",
  "subject": "PE Documents - Capital Call",
  "hasAttachments": true,
  "attachmentsCount": 1,
  "emailBody": "<html>...</html>",
  "attachmentPaths": [
    {"path": "AAMkADI5.../invoice.pdf", "source": "attachment"}
  ],
  "relevanceCheck": {
    "isRelevant": true,
    "confidence": 0.95,
    "reasoning": "Subject mentions PE documents..."
  },
  "classification": {
    "category": "Capital Call",
    "confidence": 0.87,
    "fund_name": "ABC Fund",
    "pe_company": "XYZ Capital",
    "reasoning": "..."
  },
  "pipelineMode": "full",
  "stepsExecuted": ["triage", "pre-processing", "classification", "routing"],
  "queue": "archival-pending",
  "processedAt": "2026-03-04T14:00:12Z",
  "updatedAt": "2026-03-04T14:00:12Z"
}
```

---

### 2. Pipeline Configuration (Environment Variables)

Not a persistent entity — read from environment at startup. Documented for completeness.

| Variable | Required | Default | Description |
|---|---|---|---|
| `PIPELINE_MODE` | No | `"full"` | Processing mode: `"full"` or `"triage-only"` |
| `TRIAGE_COMPLETE_QUEUE` | No | `"triage-complete"` | Output queue name in triage-only mode |
| `TRIAGE_COMPLETE_SB_NAMESPACE` | No | (uses primary `SERVICEBUS_NAMESPACE`) | External Service Bus namespace for IDP integration |

**Validation rules**:
- `PIPELINE_MODE` must be `"full"` or `"triage-only"` (case-insensitive). Invalid values logged as error, defaulted to `"full"`.
- `TRIAGE_COMPLETE_QUEUE` must be a non-empty string if set. If empty/whitespace, falls back to `"triage-complete"`.
- `TRIAGE_COMPLETE_SB_NAMESPACE` is a bare namespace name (without `.servicebus.windows.net`). The SDK appends the suffix.

---

### 3. Triage-Complete Queue Message

**Queue**: Configurable via `TRIAGE_COMPLETE_QUEUE` (default: `triage-complete`)  
**Namespace**: Configurable via `TRIAGE_COMPLETE_SB_NAMESPACE` (default: primary)  
**Producer**: Python email classifier agent (triage-only mode)  
**Consumer**: External IDP system

This is a new message type for emails that passed relevance triage but were not classified.

#### Message Schema

```json
{
  "emailId": "string (Graph API message ID)",
  "from": "string (sender email)",
  "subject": "string",
  "receivedAt": "string (ISO 8601)",
  "hasAttachments": "boolean",
  "attachmentsCount": "integer",
  "attachmentPaths": [
    {
      "path": "string ({emailId}/{filename})",
      "source": "string ('attachment' | 'link')"
    }
  ],
  "relevance": {
    "isRelevant": true,
    "confidence": "float (0.0–1.0)",
    "initialCategory": "string (best-guess category from relevance step)",
    "reasoning": "string"
  },
  "pipelineMode": "triage-only",
  "status": "triaged",
  "processedAt": "string (ISO 8601)",
  "routing": {
    "sourceQueue": "email-intake",
    "targetQueue": "string (queue name)",
    "routedAt": "string (ISO 8601)"
  }
}
```

**Key differences from existing `archival-pending` / `human-review` messages**:
- No `category`, `confidence`, `fund_name`, `pe_company` fields (classification was skipped).
- Includes `relevance` block with triage results.
- `pipelineMode` is always `"triage-only"`.

---

### 4. No Schema Changes

The following entities are **not affected** by this feature:

| Entity | Reason |
|---|---|
| `email-intake` queue message | Payload is the same regardless of pipeline mode |
| `discarded` queue message | Routing logic is the same in both modes |
| `human-review` queue message | In triage-only mode, low-confidence relevance emails still route here with the same format |
| `pe-events` container | Only written during classification (Step 4a), which is skipped in triage-only mode |
| `extracted-data` container | Written during attachment processing, which runs in both modes |
| Logic App workflow | No changes needed; it produces intake messages regardless of agent mode |
