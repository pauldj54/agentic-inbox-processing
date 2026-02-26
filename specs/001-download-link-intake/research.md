# Research: Download-Link Intake

**Feature**: 001-download-link-intake  
**Date**: 2026-02-26

## 1. URL Detection in Email Bodies

### Decision
Two-phase approach: (1) regex extraction from email body + file extension filter on URL path, (2) optional HTTP HEAD probe for ambiguous URLs.

### Rationale
- File-extension matching (`\.pdf|\.docx?|\.xlsx?|\.csv`) catches the majority of document download links with zero latency.
- HEAD probes are only needed for extension-less URLs and add network overhead; keeping them optional and controlled by a short timeout (5s) limits impact.
- HTML email bodies are parsed with `html.parser` (stdlib) to extract `href` attributes accurately; plain-text bodies use a simple URL regex.
- A small denylist of non-document domains (social media, tracking pixels) prevents wasted HEAD requests.
- Cloud-hosted links (SharePoint, Dropbox, etc.) are recognized for logging but treated as download failures per Assumption #1 (unauthenticated HTTPS only).

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| `beautifulsoup4` for HTML parsing | Adds a dependency; `html.parser` from stdlib is sufficient for `href` extraction |
| `urlextract` / `tldextract` libraries | Over-engineering; simple regex is adequate per CAR-001 |
| NLP-based link classification | Spec explicitly says "start with URL patterns matching common file extensions" |
| HEAD request for every URL | Too slow; extension-first filtering is cheaper |

### URL Regex
```
https?://[^\s"'<>)\]]+
```
For HTML bodies, prefer: `href=["'](https?://[^"']+)["']`

### Document Extension Filter (applied to `urlparse(url).path`)
```
\.(pdf|docx?|xlsx?|csv|pptx?|txt|zip)(\?.*)?$
```

### Filename Derivation Priority
1. `Content-Disposition` header `filename` parameter
2. Last segment of URL path (after stripping query params)
3. Generated fallback: `download_{short_uuid}.{ext}` (extension from `Content-Type` via `mimetypes.guess_extension()`)

---

## 2. HTTP Download with aiohttp

### Decision
Use existing `aiohttp` dependency for all HTTP downloads. No new HTTP library needed.

### Rationale
- `aiohttp` is already in `requirements.txt` and supports streaming downloads, Content-Type detection, Content-Disposition parsing, size-limit enforcement, configurable timeouts, and redirect following.
- Aligns with CAR-004 (use existing project capabilities before adding dependencies).
- The project already uses async patterns (`email_classifier_agent.py`, `graph_tools.py`), so aiohttp's async API is natural.

### Capability Confirmation
| Requirement | aiohttp Support |
|---|---|
| Content-Type detection | `response.content_type` |
| Content-Disposition filename | `response.content_disposition.filename` |
| Size limit (Content-Length) | `response.content_length` pre-check |
| Size limit (streaming) | `response.content.iter_chunked(8192)` with byte counter |
| Timeout (30s per download) | `aiohttp.ClientTimeout(total=30)` |
| Redirect following | Default enabled (up to 10 hops) |

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| `httpx` | Adds a new dependency; aiohttp already satisfies all requirements |
| `requests` | Synchronous; would block the async event loop |
| `urllib3` | No native async support |

---

## 3. Azure Blob Storage Upload (Python SDK)

### Decision
Use `azure-storage-blob` async SDK with `DefaultAzureCredential`. This is one new dependency (official Microsoft SDK per CAR-006).

### Rationale
- `azure-storage-blob` is the official SDK for Blob Storage operations, following the project's convention (CAR-006).
- `DefaultAzureCredential` is already used for Cosmos DB, Service Bus, and Graph API across the project (CAR-005).
- The async variant (`azure.storage.blob.aio.BlobServiceClient`) aligns with the project's async patterns and avoids blocking the event loop during upload.
- The storage account name (`stdocprocdevizr2ch55`) and container (`attachments`) are already known from the Logic App workflow.
- RBAC role `Storage Blob Data Contributor` is required; already assigned for the Logic App identity.

### Configuration
- **Package**: `azure-storage-blob>=12.19.0`
- **Account URL**: `https://stdocprocdevizr2ch55.blob.core.windows.net` (derive from env var `STORAGE_ACCOUNT_NAME` or `STORAGE_ACCOUNT_URL`)
- **Container**: `attachments`
- **Blob path**: `{emailId}/{filename}`
- **Content-Type**: Set from download's Content-Type header via `ContentSettings`
- **Overwrite**: `True` for idempotency

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Upload via Logic App Blob connector from Python | Adds coupling; direct SDK is simpler |
| SAS token auth | Secret management burden; DefaultAzureCredential is project standard |
| Azure Storage REST API directly | Lower-level; SDK handles auth, retries, chunking |

---

## 4. Logic App `attachmentPaths` Schema Migration

### Decision
Change the `Append_to_AttachmentPaths` action's `value` from a string to an inline JSON object `{"path": "...", "source": "attachment"}`.

### Rationale
- Logic Apps `AppendToArrayVariable` natively supports JSON objects in the `value` field — no expression workaround needed.
- The Service Bus send action uses `variables('AttachmentPaths')` which serializes the array of objects correctly as JSON.
- The Cosmos DB upsert action similarly passes the array directly.
- This is a **breaking change** for downstream consumers that currently expect flat strings — all consumers must be updated in the same release.

### workflow.json Change
```json
// Before (string):
"value": "@{triggerOutputs()?['body/id']}/@{item()?['name']}"

// After (object):
"value": {
    "path": "@{triggerOutputs()?['body/id']}/@{item()?['name']}",
    "source": "attachment"
}
```

### Downstream Impact
| Consumer | Change Required |
|---|---|
| `email_classifier_agent.py` | Update `attachmentPaths` iteration: access `.get("path")` instead of using string directly |
| `cosmos_tools.py` | Update any code that reads/writes `attachmentPaths` |
| `webapp/main.py` + `dashboard.html` | Update attachment display to read from object and show `source` indicator |
| `queue_tools.py` | Review — may iterate over `attachmentPaths` for routing |

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Keep flat strings, transform in Python | Inconsistent schema at source; all consumers need transformation logic |
| Add a separate `attachmentSources` array | Parallel arrays are fragile and error-prone |
| Use `Compose` action per iteration | Extra action per loop iteration; inline object is cleaner |

---

## 5. Download Failure Tracking (Open Design Decision)

### Decision
Record download failures as lightweight metadata in the Cosmos DB email document, within an optional `downloadFailures` array.

### Rationale
- Clarification Q2 (whether to track failures in Cosmos DB) was posed during the spec clarification session but the user pivoted to plan creation without answering.
- For operational visibility (US2, FR-008) and diagnosability, storing failure metadata alongside the email document is the simplest approach — no separate container, no separate schema.
- The array is optional (absent if no download was attempted or all succeeded), keeping the happy path clean.
- This is a **plan recommendation**, not a spec mandate. If the user disagrees during task execution, failures can be logged only (not persisted in Cosmos DB).

### Proposed Schema
```json
{
  "downloadFailures": [
    {
      "url": "https://example.com/report.pdf",
      "error": "HTTP 404",
      "attemptedAt": "2026-02-26T10:30:00Z"
    }
  ]
}
```

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Log-only (no Cosmos DB persistence) | Loses queryability; operators can't filter emails by download failure in the dashboard |
| Separate `download-failures` container | Over-engineering for a simple array of errors; violates CAR-001 |
| Store in `audit-logs` container | Possible but fragments the email's story across containers; embedded is simpler to query |
