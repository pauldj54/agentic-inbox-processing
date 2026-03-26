# Data Model: Triage Consumer Client

**Feature**: 005-triage-consumer
**Date**: 2025-07-17

## Naming Conventions

This feature operates at two data boundaries with distinct naming conventions:

| Boundary | Convention | Rationale |
|---|---|---|
| **Triage Message** (Service Bus queue input) | **camelCase** | Established by the producer (`email_classifier_agent.py`). Consistent across all top-level and nested fields. Read-only contract — consumer MUST NOT alter the schema. |
| **`attachmentPaths` element objects** | **Mixed** (pass-through) | These objects originate from multiple upstream sources (Logic App, download processor, SFTP handler), each with its own schema. The consumer handles all variants: `local_link`, `blobUrl`, `path`, `source`, `url`, `content_type`, `name`, `size`. |
| **API Request Payload** (HTTP output) | **snake_case** | Outbound boundary owned by the consumer. Follows Python/REST API conventions. |

## Entities

This feature does not modify existing data models. It consumes an existing message format from the `triage-complete` queue and produces an API request payload for an external system. Both structures are documented here as read/write contracts.

---

### 1. Triage Message (input — consumed from Service Bus queue)

**Queue**: `triage-complete`
**Direction**: Inbound (read-only)
**Producer**: `email_classifier_agent.py` (lines 426–460)

This is the existing message schema. The consumer MUST NOT modify this schema.

#### Core Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `emailId` | `string` | Yes | Unique email/document identifier (Graph API message ID or SFTP-generated UUID) |
| `from` | `string` | Yes | Sender email address or SFTP source identifier |
| `subject` | `string` | Email only | Email subject line |
| `receivedAt` | `string` (ISO 8601) | Yes | When the document was received |
| `hasAttachments` | `boolean` | Yes | Whether the document has file attachments |
| `attachmentsCount` | `integer` | Yes | Number of attachments |
| `attachmentPaths` | `array` | Yes | Attachment metadata (see below) |
| `intakeSource` | `string` | Yes | `"email"` or `"sftp"` |
| `pipelineMode` | `string` | Yes | Always `"triage-only"` for this queue |
| `status` | `string` | Yes | Always `"triaged"` |
| `processedAt` | `string` (ISO 8601) | Yes | When triage processing completed |

#### Nested: `relevance`

| Field | Type | Required | Description |
|---|---|---|---|
| `isRelevant` | `boolean` | Yes | Whether the document was deemed relevant |
| `confidence` | `float` | Yes | Confidence score (0.0–1.0) |
| `initialCategory` | `string` | Yes | Initial classification category |
| `reasoning` | `string` | Yes | LLM reasoning for the classification |

#### Nested: `routing`

| Field | Type | Required | Description |
|---|---|---|---|
| `sourceQueue` | `string` | Yes | Queue the message was routed from |
| `targetQueue` | `string` | Yes | Queue the message was routed to |
| `routedAt` | `string` (ISO 8601) | Yes | When routing occurred |

#### SFTP-specific fields (present only when `intakeSource` = `"sftp"`)

| Field | Type | Required | Description |
|---|---|---|---|
| `originalFilename` | `string` | SFTP only | Original filename from SFTP upload |
| `fileType` | `string` | SFTP only | File extension/type |
| `blobPath` | `string` | SFTP only | Blob storage path for the uploaded file |

#### `attachmentPaths` element format

Attachment entries can be **either** objects or plain strings (backward compatibility). Object fields vary by upstream source — the consumer handles all variants defensively.

**Object format** (fields vary by source — all optional except `path`):

| Field | Type | Convention | Source | Description |
|---|---|---|---|---|
| `name` | `string` | — | Logic App | Attachment filename |
| `local_link` | `string` | snake_case | Logic App | Local/blob URL to the attachment |
| `blobUrl` | `string` | camelCase | Logic App | Azure Blob Storage URL (alternative to local_link) |
| `path` | `string` | — | All sources | Path identifier (universal fallback) |
| `source` | `string` | — | Download / SFTP | Origin indicator: `"link"`, `"sftp"` |
| `url` | `string` | — | Download | Original download URL |
| `content_type` | `string` | snake_case | Download | MIME type of downloaded file |
| `size` | `integer` | — | Logic App | File size in bytes |

**URL resolution priority** (consumer logic): `local_link` → `blobUrl` → `path`

**String format** (legacy): Plain URL or path string.

#### Example: Email triage message

```json
{
  "emailId": "AAMkADI5NmFl...",
  "from": "investments@example.com",
  "subject": "Capital Call - Fonds Immobilier III",
  "receivedAt": "2025-07-17T10:30:00Z",
  "hasAttachments": true,
  "attachmentsCount": 2,
  "attachmentPaths": [
    {
      "name": "Capital_Call_Statement.pdf",
      "local_link": "https://stdocprocdevizr2ch55.blob.core.windows.net/attachments/AAMkADI5NmFl.../Capital_Call_Statement.pdf",
      "size": 245760
    },
    {
      "name": "Distribution_Notice.pdf",
      "local_link": "https://stdocprocdevizr2ch55.blob.core.windows.net/attachments/AAMkADI5NmFl.../Distribution_Notice.pdf",
      "size": 102400
    }
  ],
  "intakeSource": "email",
  "relevance": {
    "isRelevant": true,
    "confidence": 0.95,
    "initialCategory": "Capital Call",
    "reasoning": "Subject contains 'Capital Call' and attachments include financial statements"
  },
  "pipelineMode": "triage-only",
  "status": "triaged",
  "processedAt": "2025-07-17T10:30:15Z",
  "routing": {
    "sourceQueue": "email-intake",
    "targetQueue": "triage-complete",
    "routedAt": "2025-07-17T10:30:15Z"
  }
}
```

#### Example: SFTP triage message

```json
{
  "emailId": "sftp-abc123-def456",
  "from": "sftp-upload",
  "receivedAt": "2025-07-17T14:00:00Z",
  "hasAttachments": true,
  "attachmentsCount": 1,
  "attachmentPaths": [
    {
      "name": "Q2_2025_NAV_Report.pdf",
      "local_link": "https://stdocprocdevizr2ch55.blob.core.windows.net/sftp-uploads/Q2_2025_NAV_Report.pdf"
    }
  ],
  "intakeSource": "sftp",
  "originalFilename": "Q2_2025_NAV_Report.pdf",
  "fileType": "pdf",
  "blobPath": "sftp-uploads/Q2_2025_NAV_Report.pdf",
  "relevance": {
    "isRelevant": true,
    "confidence": 0.88,
    "initialCategory": "NAV Statement",
    "reasoning": "Filename indicates NAV report, common PE document type"
  },
  "pipelineMode": "triage-only",
  "status": "triaged",
  "processedAt": "2025-07-17T14:00:10Z",
  "routing": {
    "sourceQueue": "email-intake",
    "targetQueue": "triage-complete",
    "routedAt": "2025-07-17T14:00:10Z"
  }
}
```

---

### 2. API Request Payload (output — sent to external API)

**Endpoint**: Configurable via `API_ENDPOINT` environment variable
**Direction**: Outbound (write-only)
**Method**: HTTP POST with `Content-Type: application/json`

#### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `documents` | `array<object>` | Yes | List of documents to process |
| `project_name` | `string` | Yes | Derived from subject (fund name heuristic) or default |
| `analysis_name` | `string` | Yes | From `DEFAULT_ANALYSIS_NAME` env var |
| `analysis_description` | `string` | Yes | Auto-generated from intake source and subject |
| `data_model_name` | `string` | Yes | From `DATA_MODEL_NAME` env var |
| `classifier_name` | `string \| null` | Yes | Always `null` (no classifier selection) |
| `language` | `string` | Yes | `"en"` or `"fr"` (detected from content) |
| `created_by` | `string` | Yes | Always `"triage_consumer"` |
| `auto_extract` | `boolean` | Yes | Always `true` |
| `_metadata` | `object` | Yes | Traceability back to original triage message |

#### Nested: `documents[]`

| Field | Type | Description |
|---|---|---|
| `sas_url` | `string` | URL to the document (from attachment path) |
| `document_name` | `string` | Human-readable document name |

#### Nested: `_metadata`

| Field | Type | Description |
|---|---|---|
| `email_id` | `string` | Original emailId from triage message |
| `intake_source` | `string` | `"email"` or `"sftp"` |
| `processed_at` | `string` | Original processedAt timestamp |

#### Example

```json
{
  "documents": [
    {
      "sas_url": "https://stdocprocdevizr2ch55.blob.core.windows.net/attachments/AAMkADI5.../Capital_Call_Statement.pdf",
      "document_name": "Capital_Call_Statement.pdf"
    }
  ],
  "project_name": "Fonds Immobilier III",
  "analysis_name": "Auto-triage Document Processing",
  "analysis_description": "Auto-processing from email intake - Capital Call - Fonds Immobilier III",
  "data_model_name": "Capital Call Statements",
  "classifier_name": null,
  "language": "fr",
  "created_by": "triage_consumer",
  "auto_extract": true,
  "_metadata": {
    "email_id": "AAMkADI5NmFl...",
    "intake_source": "email",
    "processed_at": "2025-07-17T10:30:15Z"
  }
}
```

## State Transitions

This feature has no state transitions. The consumer is stateless — it reads a message, displays it, forwards it to an API, and completes it. There is no local persistence or state machine.
