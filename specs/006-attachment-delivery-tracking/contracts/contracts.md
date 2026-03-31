# Interface Contracts: Attachment Delivery Tracking

**Feature**: 006-attachment-delivery-tracking  
**Date**: 2026-03-30

## Contract 1: Email Logic App — Dedup Query Action

### `Query_dedup_by_hash` (Cosmos DB Query Action)

**Purpose**: Find existing intake record with matching `contentHash` in the same partition.

**Input**:
- `contentHash`: Base64-encoded MD5 from `Get_attachment_md5` HTTP HEAD response
- `partitionKey`: `{senderDomain}_{YYYY-MM}` (already computed for the email record)

**Cosmos DB Query**:
```sql
SELECT TOP 1 * FROM c WHERE c.contentHash = @contentHash
```

**Headers**:
```json
{
  "x-ms-documentdb-raw-partitionkey": "\"<partitionKey>\"",
  "x-ms-max-item-count": "1"
}
```

**Output Routing**:
- **0 results** → New record: create with `version: 1`, `deliveryCount: 1`, delivery history `action: "new"`
- **≥1 results** → Compare content hash:
  - Same hash → Duplicate: increment `deliveryCount`, append history `action: "duplicate"`
  - Different hash (same filename match) → Update: increment `version` + `deliveryCount`, update `contentHash`, append history `action: "update"`

---

## Contract 2: Email Logic App — Delivery Tracking Fields on Cosmos Upsert

### New Record Body (replacing current `Create_or_update_email_document`)

The existing Cosmos upsert action body is extended with these additional fields:

```json
{
  "contentHash": "@{variables('PrimaryContentHash')}",
  "version": 1,
  "deliveryCount": 1,
  "deliveryHistory": [
    {
      "deliveredAt": "@{utcNow()}",
      "contentHash": "@{variables('PrimaryContentHash')}",
      "action": "new"
    }
  ],
  "lastDeliveredAt": "@{utcNow()}"
}
```

### Duplicate Patch Body

```json
{
  "deliveryCount": "@add(body('Query_dedup_by_hash')?['value'][0]['deliveryCount'], 1)",
  "deliveryHistory": "@union(body('Query_dedup_by_hash')?['value'][0]['deliveryHistory'], json(concat('[{\"deliveredAt\":\"', utcNow(), '\",\"contentHash\":\"', variables('PrimaryContentHash'), '\",\"action\":\"duplicate\"}]')))",
  "lastDeliveredAt": "@{utcNow()}"
}
```

### Content Update Patch Body

```json
{
  "contentHash": "@{variables('PrimaryContentHash')}",
  "version": "@add(body('Query_dedup_by_hash')?['value'][0]['version'], 1)",
  "deliveryCount": "@add(body('Query_dedup_by_hash')?['value'][0]['deliveryCount'], 1)",
  "deliveryHistory": "@union(body('Query_dedup_by_hash')?['value'][0]['deliveryHistory'], json(concat('[{\"deliveredAt\":\"', utcNow(), '\",\"contentHash\":\"', variables('PrimaryContentHash'), '\",\"action\":\"update\"}]')))",
  "lastDeliveredAt": "@{utcNow()}"
}
```

---

## Contract 3: Python — `find_by_content_hash()` Helper

### Location: `src/agents/tools/cosmos_tools.py`

```python
def find_by_content_hash(
    self,
    content_hash: str,
    partition_key: str,
) -> dict | None:
    """Find an existing intake record by contentHash within a partition.

    Args:
        content_hash: Base64-encoded MD5 hash to search for.
        partition_key: Cosmos DB partition key ({domain}_{YYYY-MM}).

    Returns:
        The matching record dict, or None if no match found.
    """
```

**Query**: `SELECT TOP 1 * FROM c WHERE c.contentHash = @contentHash`
**Parameters**: `[{"name": "@contentHash", "value": content_hash}]`
**Partition key**: Passed directly (no cross-partition query).

---

## Contract 4: Python Link Download Tool — Content Hash Capture

### Location: `src/agents/tools/link_download_tool.py`

After blob upload in `_download_and_upload()`, compute MD5 from in-memory bytes:

```python
import hashlib
import base64

content_md5 = base64.b64encode(hashlib.md5(file_data).digest()).decode()
```

The `DownloadedFile` dataclass is extended:

```python
@dataclass
class DownloadedFile:
    path: str
    source: str
    url: str
    content_type: str
    content_md5: str | None = None  # NEW: base64-encoded MD5
```

---

## Contract 5: Dashboard Badge Rendering

### Location: `src/webapp/templates/dashboard.html` (~line 219)

**Before**:
```html
{% if email.intakeSource == 'sftp' %}
```

**After**:
```html
{% if email.version is defined and email.version is not none %}
```

No other template changes. The existing badge rendering logic (`v{{ ver }}`, `{{ dc }}x`) applies universally.

---

## Contract 6: Filename-Match Query for Content Update Detection

### Used by: US4 (T019, T020)

**Purpose**: When content-hash dedup returns no match, a secondary query checks for an existing record with the same filename in the same partition. If found with a different hash, the system routes to the content-update path (version increment).

### Logic App (email-ingestion/workflow.json)

Query action (added inside the "no hash match" branch of `Handle_email_dedup`):

```json
{
  "Check_filename_match": {
    "type": "ApiConnection",
    "inputs": {
      "host": { "connection": { "name": "@parameters('$connections')['documentdb']['connectionId']" } },
      "method": "post",
      "path": "/v2/cosmosdb/@{encodeURIComponent('cosmos-db-id')}/dbs/@{encodeURIComponent('document-processing')}/colls/@{encodeURIComponent('intake-records')}/query",
      "body": {
        "query": "SELECT TOP 1 * FROM c WHERE ARRAY_CONTAINS(c.attachmentPaths, {'originalName': @originalName}, true)",
        "parameters": [
          { "name": "@originalName", "value": "@{variables('primaryAttachmentName')}" }
        ]
      },
      "headers": {
        "x-ms-documentdb-partitionkey": "[\"@{variables('partitionKey')}\"]"
      }
    }
  }
}
```

### Python (cosmos_tools.py)

```python
def find_by_filename(filename: str, partition_key: str) -> dict | None:
    """Find an existing intake record with a matching attachment filename in the given partition."""
    query = (
        "SELECT TOP 1 * FROM c "
        "WHERE ARRAY_CONTAINS(c.attachmentPaths, {\"originalName\": @name}, true)"
    )
    params: list[dict[str, str]] = [{"name": "@name", "value": filename}]
    results = list(
        container.query_items(
            query=query,
            parameters=params,
            partition_key=partition_key,
        )
    )
    return results[0] if results else None
```

**Constraint**: Partition-scoped query only. No cross-partition filename lookups.
