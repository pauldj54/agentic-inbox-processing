# Contracts: Pipeline Configuration

**Feature**: 002-pipeline-config  
**Date**: 2026-03-04

## 1. Triage-Complete Queue Message Contract

**Queue**: Configurable via `TRIAGE_COMPLETE_QUEUE` (default: `triage-complete`)  
**Namespace**: Configurable via `TRIAGE_COMPLETE_SB_NAMESPACE` (default: primary `SERVICEBUS_NAMESPACE`)  
**Producer**: Python email classifier agent (`email_classifier_agent.py`, triage-only mode)  
**Consumer**: External IDP (Intelligent Document Processing) system

### Message Schema

```json
{
  "emailId": "string",
  "from": "string",
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
    "initialCategory": "string",
    "reasoning": "string"
  },
  "pipelineMode": "triage-only",
  "status": "triaged",
  "processedAt": "string (ISO 8601)",
  "routing": {
    "sourceQueue": "email-intake",
    "targetQueue": "string",
    "routedAt": "string (ISO 8601)"
  }
}
```

### Key Differences from Existing Queue Messages

| Aspect | archival-pending / human-review | triage-complete |
|---|---|---|
| `category` field | Present (PE event type) | Absent (classification skipped) |
| `confidence` (classification) | Present | Absent |
| `fund_name`, `pe_company` | Present | Absent |
| `relevance` block | Absent (embedded in classification_details) | Present (top-level) |
| `pipelineMode` | `"full"` | `"triage-only"` |
| `classification_details` | Present | Absent |

### Consumer Integration Notes

- **Authentication**: The consumer (IDP system) must authenticate to its own Service Bus namespace with `Azure Service Bus Data Receiver` role.
- **Message format**: JSON, UTF-8 encoded, `content_type: application/json`.
- **Ordering**: Messages are not ordered. The consumer must handle out-of-order delivery.
- **Idempotency**: The consumer should use `emailId` as the deduplication key.

---

## 2. Cosmos DB Email Document Contract (Extended)

**Container**: `emails`  
**Partition key**: `/status`  
**Producers**: Logic App (initial), Python agent (enrichment)

### New Fields (added by pipeline config feature)

| Field | Type | When Present | Description |
|---|---|---|---|
| `pipelineMode` | `string` | Always (after this feature) | `"full"` or `"triage-only"` |
| `stepsExecuted` | `string[]` | Always (after this feature) | Ordered list of steps completed |

### Backward Compatibility

- Existing documents without `pipelineMode` are treated as `"full"` by the dashboard.
- Existing documents without `stepsExecuted` are treated as having all steps executed.
- No migration required. New fields are written on next processing.

See [data-model.md](data-model.md) for full schema details and examples.

---

## 3. QueueTools Interface Extension

**Module**: `src/agents/tools/queue_tools.py`  
**Consumer**: `email_classifier_agent.py`

### New Method: `send_to_triage_queue()`

```python
def send_to_triage_queue(self, message_data: dict) -> str:
    """
    Send a message to the triage-complete queue.
    Uses the external Service Bus namespace if TRIAGE_COMPLETE_SB_NAMESPACE is set,
    otherwise uses the primary namespace.
    
    Args:
        message_data: Triage-complete message payload (see contract §1)
    
    Returns:
        The target queue name (for logging/routing metadata)
    
    Raises:
        Exception: If the send fails (caller should handle, e.g., dead-letter)
    """
```

### New Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `triage_queue` | `str \| None` | `os.environ.get("TRIAGE_COMPLETE_QUEUE", "triage-complete")` | Queue name for triage-only output |
| `triage_sb_namespace` | `str \| None` | `os.environ.get("TRIAGE_COMPLETE_SB_NAMESPACE")` | Optional external namespace |

### New Internal Method: `_get_triage_sync_client()`

Returns a `ServiceBusClient` for the triage namespace (external if set, primary otherwise). Uses `DefaultAzureCredential` for both.

---

## 4. EmailClassificationAgent Interface Extension

**Module**: `src/agents/email_classifier_agent.py`

### New Instance Attribute

| Attribute | Type | Source | Description |
|---|---|---|---|
| `pipeline_mode` | `str` | `os.getenv("PIPELINE_MODE", "full")` | Active pipeline mode |

### Modified Method: `process_next_email()`

**Change**: After Step 2 (attachment processing), check `self.pipeline_mode`. If `"triage-only"`:
1. Build triage-complete message (see contract §1)
2. Call `self.queue_tools.send_to_triage_queue()`
3. Update Cosmos with `pipelineMode="triage-only"`, `stepsExecuted=["triage", "pre-processing", "routing"]`
4. Return early (skip Steps 3–5)

If `"full"` (default): continue existing flow unchanged.

---

## 5. Dashboard API Contract

**Module**: `src/webapp/main.py`  
**Route**: `GET /` (dashboard)

### New Template Context Variable

| Variable | Type | Source | Description |
|---|---|---|---|
| `pipeline_mode` | `str` | `os.environ.get("PIPELINE_MODE", "full")` | Current pipeline mode |

### Template Display Rules

| Condition | Display |
|---|---|
| `pipeline_mode == "full"` | Badge: "Full Pipeline" |
| `pipeline_mode == "triage-only"` | Badge: "Triage Only" |
| Email `stepsExecuted` lacks `"classification"` | Classification column: "Skipped (triage-only)" |
| Email has no `pipelineMode` field | Treat as `"full"` (backward compat) |
