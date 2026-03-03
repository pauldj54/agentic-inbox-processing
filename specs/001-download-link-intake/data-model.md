# Data Model: Download-Link Intake

**Feature**: 001-download-link-intake  
**Date**: 2026-02-26

## Entity Changes

### 1. Email Document (Cosmos DB `emails` container)

**Container**: `emails`  
**Partition key**: `/status`  
**Change type**: Schema migration (breaking)

#### Field: `attachmentPaths`

| Aspect | Before | After |
|---|---|---|
| Type | `string[]` | `object[]` |
| Example | `["AAMk.../invoice.pdf"]` | `[{"path": "AAMk.../invoice.pdf", "source": "attachment"}]` |

**Object schema**:

| Field | Type | Required | Values | Description |
|---|---|---|---|---|
| `path` | `string` | Yes | `{emailId}/{filename}` | Blob path relative to `/attachments` container |
| `source` | `string` | Yes | `"attachment"` \| `"link"` | Origin of the file |

**Validation rules**:
- `path` must be non-empty and follow the pattern `{emailId}/{filename}`
- `source` must be exactly `"attachment"` or `"link"`
- Array may be empty `[]` if no attachments exist

#### Field: `downloadFailures` (new, optional)

| Field | Type | Required | Description |
|---|---|---|---|
| `downloadFailures` | `object[]` | No | Present only when link download was attempted and failed |

**Object schema**:

| Field | Type | Required | Description |
|---|---|---|---|
| `url` | `string` | Yes | The URL that was attempted |
| `error` | `string` | Yes | Error description (e.g., "HTTP 404", "Timeout after 30s", "Unsupported content-type: text/html") |
| `attemptedAt` | `string` (ISO 8601) | Yes | Timestamp of the download attempt |

#### Updated field interactions

| Field | Impact |
|---|---|
| `hasAttachments` | Now reflects both traditional + link-sourced attachments. `True` if `len(attachmentPaths) > 0` |
| `attachmentsCount` | Sum of all entries in `attachmentPaths` (regardless of `source`) |

#### Full email document example (post-migration)

```json
{
  "id": "AAMkADI5...",
  "status": "received",
  "confidenceLevel": "pending",
  "receivedAt": "2026-02-26T10:00:00Z",
  "from": "sender@example.com",
  "subject": "PE Documents - Capital Call",
  "hasAttachments": true,
  "attachmentsCount": 2,
  "emailBody": "<html>...<a href='https://portal.example.com/report.pdf'>Download Report</a>...</html>",
  "attachmentPaths": [
    {"path": "AAMkADI5.../invoice.pdf", "source": "attachment"},
    {"path": "AAMkADI5.../report.pdf", "source": "link"}
  ],
  "downloadFailures": [],
  "processedAt": null,
  "classification": null,
  "archivalPath": null
}
```

---

### 2. Service Bus Intake Message (`email-intake` queue)

**Change type**: Schema migration (breaking, coordinated with Logic App)

#### Field: `attachmentPaths`

Same migration as Cosmos DB: flat string array → array of objects.

| Aspect | Before | After |
|---|---|---|
| Type | `string[]` | `object[]` |
| Format | `["emailId/filename"]` | `[{"path": "emailId/filename", "source": "attachment"}]` |

**Note**: The Logic App produces this message. After the `workflow.json` change, the message will contain objects. The Python agent (`email_classifier_agent.py`) must be updated to read `.get("path")` from each entry.

#### Full message example (post-migration)

```json
{
  "emailId": "AAMkADI5...",
  "from": "sender@example.com",
  "subject": "PE Documents - Capital Call",
  "bodyText": "Please find the attached documents...",
  "receivedAt": "2026-02-26T10:00:00Z",
  "attachmentsCount": 1,
  "hasAttachments": true,
  "attachmentPaths": [
    {"path": "AAMkADI5.../invoice.pdf", "source": "attachment"}
  ],
  "priority": "normal",
  "source": "logic-app-ingestion"
}
```

---

### 3. Attachment Blob (Azure Blob Storage)

**Container**: `attachments`  
**Storage account**: `stdocprocdevizr2ch55`

**No schema change** to the blob itself. The blob is always a raw file.

| Aspect | Detail |
|---|---|
| Path convention | `{emailId}/{filename}` (unchanged) |
| New content | Link-sourced files stored at the same path |
| Content-Type header | Set from the download response's `Content-Type` |
| Metadata | No custom blob metadata required |

---

## Migration Notes

### Backward Compatibility

This is a **breaking change** to the `attachmentPaths` field. All components must be updated in a single coordinated deployment:

| Component | File | Change |
|---|---|---|
| Logic App | `workflow.json` | `attachmentPaths` entries become objects |
| Classifier agent | `email_classifier_agent.py` | Read `.get("path")` from each entry |
| Cosmos tools | `cosmos_tools.py` | Handle object format when reading/writing `attachmentPaths` |
| Dashboard backend | `webapp/main.py` | Pass object format to template |
| Dashboard template | `dashboard.html` | Render `source` indicator |
| Queue tools | `queue_tools.py` | Review attachment path handling |

### Data Migration

Existing Cosmos DB documents with flat-string `attachmentPaths` will NOT be migrated. The code must handle both formats gracefully during a transition period:

```python
# Backward-compatible reading
for entry in email.get("attachmentPaths", []):
    if isinstance(entry, str):
        path = entry
        source = "attachment"  # assume legacy
    else:
        path = entry.get("path", "")
        source = entry.get("source", "attachment")
```

This dual-format handling should be maintained for at least one release cycle, then removed once all documents have been reprocessed.

---

## State Transitions

### Email Processing with Link Download

```
[Email arrives] → Logic App processes traditional attachments
                  → attachmentPaths: [{"path":"...", "source":"attachment"}]
                  → Cosmos DB upsert (status: "received")
                  → Service Bus message sent

[Agent receives from queue]
  → Pre-processing: scan emailBody for download URLs
  → For each detected URL:
      → Download file (aiohttp, 30s timeout, 50MB limit)
      → On success: upload to Blob Storage, append {"path":"...", "source":"link"} to attachmentPaths
      → On failure: append to downloadFailures, log error
  → Update Cosmos DB with enriched attachmentPaths + downloadFailures
  → Proceed to classification (existing flow)
```
