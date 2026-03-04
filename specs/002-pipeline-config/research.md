# Research: Pipeline Configuration

**Feature**: 002-pipeline-config  
**Date**: 2026-03-04

## 1. Pipeline Mode Branching Strategy

### Decision
Insert a simple if/else branch in `process_next_email()` between Step 2 (attachment processing, line ~353) and Step 3 (classification, line ~354). In triage-only mode, skip Steps 3–5 entirely and route directly to the triage-complete queue.

### Rationale
- The branching point is clean: after all pre-processing (relevance check, link downloads, attachments/OCR) is complete but before classification is invoked.
- A single if/else keeps the code linear and readable (CAR-001).
- No refactoring needed — the existing method structure supports an early return after pre-processing.
- The `pipeline_mode` value is read once at startup and stored on the agent instance, so no per-email env var reads.

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Strategy pattern (separate classes per mode) | Over-engineering per CAR-001; a simple branch is sufficient |
| Decorator/middleware pattern | Adds indirection; the branching logic is a single check |
| Process method per mode (`process_full()`, `process_triage()`) | Duplicates shared pre-processing code; harder to maintain |

### Exact Insertion Point
```python
# After Step 2 (attachment processing) — line ~353:
#     self.cosmos_tools.log_classification_event(...)
#
# BRANCH: if pipeline_mode == "triage-only":
#     → route to triage-complete queue
#     → update Cosmos with pipelineMode="triage-only", stepsExecuted=[...]
#     → return early
#
# Otherwise continue to Step 3 (classification) — line ~354
```

---

## 2. External Service Bus Client for IDP Integration

### Decision
Add optional `triage_sb_namespace` to `QueueTools.__init__()`. Create a lazy `_get_triage_sync_client()` method that builds a `ServiceBusClient` for the external namespace using `DefaultAzureCredential`. Add a dedicated `send_to_triage_queue()` method that uses either the triage client (if external namespace is set) or the primary client.

### Rationale
- Lazy creation avoids unnecessary connection overhead when running in full-pipeline mode.
- `DefaultAzureCredential` is used for consistency (CAR-005, constitution).
- The deploying identity needs `Azure Service Bus Data Sender` RBAC role on the external namespace.
- A dedicated method (`send_to_triage_queue`) is cleaner than modifying the generic `_send_to_queue` — it encapsulates the external-vs-primary decision.

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Pass namespace per call to `_send_to_queue()` | Leaks infrastructure concern into the caller |
| Create the external client eagerly in `__init__` | Wasteful when running in full mode; fails startup if external namespace is unreachable |
| Use a connection string instead of namespace name | Violates Entra-first auth policy (constitution IV) |
| Single `_send_to_queue()` with optional namespace param | Clutters the common code path; triage routing is a distinct operation |

### Implementation Pattern
```python
class QueueTools:
    def __init__(self, namespace=None, triage_queue=None, triage_sb_namespace=None):
        # ... existing init ...
        self.triage_queue = triage_queue or os.environ.get("TRIAGE_COMPLETE_QUEUE", "triage-complete")
        self._triage_sb_namespace = triage_sb_namespace or os.environ.get("TRIAGE_COMPLETE_SB_NAMESPACE")
    
    def _get_triage_sync_client(self):
        """Get SB client for the triage queue — external namespace if set, else primary."""
        ns = self._triage_sb_namespace or self.namespace
        fqns = f"{ns}.servicebus.windows.net"
        return ServiceBusClient(fully_qualified_namespace=fqns, credential=DefaultAzureCredential())
    
    def send_to_triage_queue(self, message_data: dict):
        """Send a message to the triage-complete queue (may be on external namespace)."""
        with self._get_triage_sync_client() as client:
            sender = client.get_queue_sender(queue_name=self.triage_queue)
            with sender:
                msg = ServiceBusMessage(body=json.dumps(message_data, default=str), content_type="application/json")
                sender.send_messages(msg)
```

---

## 3. Pipeline Mode Configuration Pattern

### Decision
Read `PIPELINE_MODE` from environment at agent startup. Validate against allowed values (`full`, `triage-only`). Default to `full` if unset. Log a warning if unset, log an error if invalid (then default to `full`). Store on the `EmailClassificationAgent` instance.

### Rationale
- Follows existing env var pattern: `load_environment()` in `run_agent.py` validates required vars and logs them. Pipeline mode is optional → no `sys.exit(1)` on absence.
- Reading once at startup (not per-email) matches the spec: "deployment-time setting; takes effect on restart."
- Validation is a simple `in` check against allowed values.

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Config file (JSON/YAML) | Spec clarification explicitly chose env var pattern |
| Database-stored config | Over-engineering for a deployment-time setting |
| CLI argument | Not suitable for Azure App Service deployments |

### Validation Logic
```python
pipeline_mode = os.getenv("PIPELINE_MODE", "full").strip().lower()
VALID_MODES = {"full", "triage-only"}
if pipeline_mode not in VALID_MODES:
    logger.error(f"Invalid PIPELINE_MODE '{pipeline_mode}'. Valid: {VALID_MODES}. Defaulting to 'full'.")
    pipeline_mode = "full"
elif not os.getenv("PIPELINE_MODE"):
    logger.warning("PIPELINE_MODE not set. Defaulting to 'full' (full-pipeline mode).")
```

---

## 4. Cosmos DB Schema Extension

### Decision
Add two fields to the email document during `update_email_classification()`: `pipelineMode` (string) and `stepsExecuted` (string array). Set them in the common section before `upsert_item()`, alongside the existing `updatedAt` field.

### Rationale
- Adding to the common section (line ~249–253 in `cosmos_tools.py`) ensures both modes write these fields.
- Fields are optional (not breaking) — existing documents without them are unaffected.
- Forward-compatible: if new modes or steps are added later, the schema accommodates them without migration.

### Field Definitions
| Field | Type | Values | Description |
|---|---|---|---|
| `pipelineMode` | string | `"full"` \| `"triage-only"` | Mode active when email was processed |
| `stepsExecuted` | string[] | `["triage", "pre-processing", "classification", "routing"]` or `["triage", "pre-processing", "routing"]` | Ordered list of steps completed |

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Separate `pipeline_runs` container | Over-engineering for a single field; email document is the natural location |
| Boolean `classificationSkipped` | Less informative than `stepsExecuted`; doesn't scale if more steps are added |
| Update only for triage-only mode | Inconsistent; both modes should record their pipeline for audit |

---

## 5. Dashboard Changes

### Decision
Pass `pipeline_mode` from `os.environ.get("PIPELINE_MODE", "full")` to the template context. Display it as a badge/label in the dashboard header. For emails processed in triage-only mode (where `stepsExecuted` lacks "classification"), show "Skipped (triage-only)" in the classification column.

### Rationale
- Minimal change per CAR-002: one label in the header, one conditional text in the classification column.
- The `pipeline_mode` is already known to the process (env var), and `stepsExecuted` is available on each email document from Cosmos DB.

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| New dashboard page/tab | Over-engineering per CAR-002 |
| Real-time mode toggle on dashboard | Not requested; mode changes require restart |

---

## 6. Error Handling for External Namespace

### Decision
When `TRIAGE_COMPLETE_SB_NAMESPACE` is set but the external namespace is unreachable (auth failure, DNS, network), catch the exception in `send_to_triage_queue()`, log the error with full details, and route the email to the dead-letter queue on the **primary** namespace for retry.

### Rationale
- Preserves the email for operational recovery (spec edge case requirement).
- Dead-lettering on the primary namespace is always available (it's the namespace the agent is already connected to).
- Logging includes the external namespace name, queue name, and error for troubleshooting.

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Crash the agent | Violates resilience; one external failure shouldn't stop all processing |
| Retry immediately | Could block the queue for other emails; DLQ + alarm is safer |
| Send to `human-review` instead | Conflates infrastructure errors with classification uncertainty |

---

## 7. Testing Approach

### Decision
Two unit tests per CAR-007:
1. **Triage-only mode**: Mock the classification step, verify it's never called, verify email is routed to triage-complete queue.
2. **Full mode (default)**: Verify classification step IS called and email routes to archival-pending or human-review.

### Rationale
- Per spec CAR-007: "Only two core tests required."
- Both tests exercise the branching logic without requiring real Azure connections.
- Mocking follows existing patterns: `patch.dict("os.environ", ...)` for config, `MagicMock` for tools.

### Test File Structure
```python
# tests/unit/test_pipeline_config.py

class TestPipelineModeRouting:
    """Tests for pipeline mode conditional branching."""
    
    async def test_triage_only_skips_classification(self):
        # Set PIPELINE_MODE=triage-only
        # Process an email through the agent
        # Assert: _classify_email NOT called
        # Assert: email sent to triage-complete queue
    
    async def test_full_mode_runs_classification(self):
        # Set PIPELINE_MODE=full (or unset)
        # Process an email through the agent
        # Assert: _classify_email IS called
        # Assert: email sent to archival-pending or human-review
```

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Integration tests with real Service Bus | Over-scoped per CAR-007; unit tests with mocks are sufficient |
| Config validation tests | Over-scoped per CAR-007; config parsing is two lines |
| External namespace connection tests | Over-scoped per CAR-007; tested manually |
