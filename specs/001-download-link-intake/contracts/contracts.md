# Contracts: Download-Link Intake

**Feature**: 001-download-link-intake  
**Date**: 2026-02-26

## 1. Service Bus Message Contract: `email-intake` Queue

**Producer**: Logic App (`email-ingestion` workflow)  
**Consumer**: Python email classifier agent (`email_classifier_agent.py`)

### Message Schema (v2 — post-migration)

```json
{
  "emailId": "string (Graph API message ID)",
  "from": "string (sender email or display name)",
  "subject": "string",
  "bodyText": "string (email body preview, may be HTML)",
  "receivedAt": "string (ISO 8601 datetime)",
  "attachmentsCount": "integer (count of traditional attachments from Logic App)",
  "hasAttachments": "boolean",
  "attachmentPaths": [
    {
      "path": "string ({emailId}/{filename})",
      "source": "string ('attachment' | 'link')"
    }
  ],
  "priority": "string ('normal')",
  "source": "string ('logic-app-ingestion')"
}
```

### Breaking Changes from v1

| Field | v1 (current) | v2 (new) |
|---|---|---|
| `attachmentPaths` | `string[]` — flat array of path strings | `object[]` — array of `{path, source}` objects |

### Consumer Migration Guide

**Before** (v1):
```python
for path in email_data.get("attachmentPaths", []):
    filename = path.split("/")[-1]
```

**After** (v2, with backward compatibility):
```python
for entry in email_data.get("attachmentPaths", []):
    if isinstance(entry, str):
        path, source = entry, "attachment"
    else:
        path = entry.get("path", "")
        source = entry.get("source", "attachment")
    filename = path.split("/")[-1]
```

---

## 2. Cosmos DB Email Document Contract

**Container**: `emails`  
**Partition key**: `/status`  
**Producers**: Logic App (initial upsert), Python agent (enrichment + classification updates)

### Enriched Fields (added by Python agent link-download step)

| Field | Type | When Present |
|---|---|---|
| `attachmentPaths[].source="link"` | object in array | When a link-sourced file was successfully downloaded |
| `downloadFailures` | `object[]` | When one or more link downloads failed |

See [data-model.md](data-model.md) for full schema details.

---

## 3. Link Download Tool Interface

**Module**: `src/agents/tools/link_download_tool.py`  
**Consumer**: `email_classifier_agent.py` (pre-processing step)

### Public Interface

```python
class LinkDownloadTool:
    """Detects download links in email bodies and downloads documents to Blob Storage."""

    def __init__(
        self,
        storage_account_url: str | None = None,
        container_name: str = "attachments",
        max_file_size_bytes: int = 50 * 1024 * 1024,  # 50 MB
        download_timeout_seconds: int = 30,
    ) -> None: ...

    async def process_email_links(
        self,
        email_id: str,
        email_body: str,
    ) -> LinkDownloadResult: ...
```

### Return Type

```python
@dataclass
class DownloadedFile:
    path: str            # Blob path: "{emailId}/{filename}"
    source: str          # Always "link"
    url: str             # Original source URL
    content_type: str    # MIME type from download response

@dataclass
class DownloadFailure:
    url: str             # URL that failed
    error: str           # Error description
    attempted_at: str    # ISO 8601 timestamp

@dataclass
class LinkDownloadResult:
    downloaded_files: list[DownloadedFile]
    failures: list[DownloadFailure]
    urls_detected: int   # Total URLs found in body
    urls_attempted: int  # URLs that matched document patterns
```

### Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `STORAGE_ACCOUNT_URL` | (required) | `https://{account}.blob.core.windows.net` |
| `LINK_DOWNLOAD_MAX_SIZE_MB` | `50` | Maximum file size per download in MB |
| `LINK_DOWNLOAD_TIMEOUT_S` | `30` | Timeout per download in seconds |

---

## 4. Dashboard API Contract

**Endpoint**: `GET /` (HTML dashboard)  
**Change**: The `emails` template variable now contains `attachmentPaths` as objects.

### Template Data Change

The `emails` list passed to `dashboard.html` will contain email documents with the new `attachmentPaths` object format. The template must handle both legacy (string) and new (object) formats during the transition period.

No new API endpoints are introduced.
