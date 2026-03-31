# Research: Attachment Delivery Tracking for Email and Download Links

**Feature**: 006-attachment-delivery-tracking  
**Date**: 2026-03-30

## R1: Email Logic App — How to Extract Content-MD5 After Blob Upload

### Question
The email Logic App uses `Create_blob_(V2)` (Azure Blob connector ApiConnection action) to upload attachments. Does this action return Content-MD5, and how do we extract it?

### Finding
The `Create_blob_(V2)` action uses the Azure Blob Storage managed connector. Unlike the HTTP-based approach used in the SFTP workflow (which does a HEAD request to get `Content-MD5` from blob headers), the managed connector's response **does not** reliably include `Content-MD5` in its output body.

**Decision**: Follow the same pattern as the SFTP workflow — add an HTTP HEAD action (`Get_blob_md5`) after the blob upload to retrieve `Content-MD5` from the blob's response headers. This is the proven approach already deployed in production for SFTP.

**Implementation**:
```json
{
  "Get_attachment_md5": {
    "type": "Http",
    "inputs": {
      "method": "HEAD",
      "uri": "https://stdocprocdevizr2ch55.blob.core.windows.net/attachments/@{items('For_each_message')?['id']}/@{items('For_each_attachment')?['name']}",
      "authentication": {
        "type": "ManagedServiceIdentity"
      }
    },
    "runAfter": { "Create_blob_(V2)": ["Succeeded"] }
  }
}
```

The Content-MD5 is then available as `outputs('Get_attachment_md5')['headers']['Content-MD5']`.

**Alternatives Considered**:
- Compute MD5 in-workflow using Logic App expressions: Not possible in Consumption tier (no inline code).
- Use blob trigger output: `Create_blob_(V2)` output doesn't include hash. Rejected.
- Use `Get_Blob_Metadata_(V2)` action: Returns custom metadata, not Content-MD5 system header. Rejected.

---

## R2: Email Dedup — Content-Hash Query vs Point-Read

### Question
SFTP uses a point-read by document ID (dedup key = base64(path)) for dedup. Email records use `messageId` as their document ID. How should email dedup work?

### Finding
Email records are created with `id: messageId`. The same document content can arrive in different emails (different message IDs), so a point-read by document ID cannot detect content duplicates. A partition-scoped Cosmos DB query by `contentHash` is required.

**Decision**: Use a SQL query within the partition:
```sql
SELECT * FROM c WHERE c.contentHash = @contentHash AND c.partitionKey = @partitionKey
```

This is efficient because:
1. Queries are scoped to a single logical partition (sender domain + year-month)
2. Partitions contain only hundreds of records at most (one sender-month)
3. `contentHash` can be indexed by Cosmos DB's default indexing policy (all paths indexed)

**In the Logic App**: Use the Cosmos DB connector's "Query documents" action with partition key header set, returning at most 1 doc. The Logic App conditional then branches on result count (0 = new, ≥1 = check for dup vs update).

**In Python (link download tool)**: Use a helper function `find_by_content_hash()` in cosmos_tools.py that runs the same query.

**Alternatives Considered**:
- Create a synthetic dedup key (e.g., hash of filename+sender): Would miss duplicate content with different filenames. Rejected.
- Cross-partition query: Spec explicitly scopes dedup to same partition. Not needed.
- Composite index on `contentHash`: Default indexing policy already indexes all fields. Not needed for partition-scoped queries.

---

## R3: Logic App — Where to Insert Dedup Logic in Email Workflow

### Question
The email workflow processes multiple attachments per message in a `For_each_attachment` loop, then creates a single Cosmos record per message. Where does the dedup logic fit?

### Finding
The current flow is:
1. `For_each_attachment` → `Check_if_not_inline` → `Check_if_allowed_type` → `Create_blob_(V2)` → `Append_to_AttachmentPaths`
2. After loop: `Create_or_update_email_document` → `Send_message_to_Service_Bus` → `Mark_as_Read`

**Decision**: Add per-attachment MD5 extraction inside the loop, then add dedup logic after the loop (before the Cosmos upsert). This mirrors the SFTP pattern where dedup happens after blob upload but before record creation.

**Flow after change**:
1. Inside `For_each_attachment`:
   - After `Create_blob_(V2)`: add `Get_attachment_md5` (HTTP HEAD)
   - Modify `Append_to_AttachmentPaths` to include `contentMd5` from the HEAD response
2. After `For_each_attachment` loop:
   - Extract primary content hash from first attachment in `AttachmentPaths`
   - `Query_dedup_by_hash`: Cosmos DB query for existing record with same `contentHash` in partition
   - `Handle_dedup_result`: Conditional branch (found → compare hash → dup/update; not found → new)
   - Either patch existing record or create new record with delivery tracking fields

**Alternatives Considered**:
- Per-attachment dedup (create separate records per attachment): Spec says email record's primary hash is from first attachment. Per-attachment dedup is via `contentMd5` in `attachmentPaths` entries, not separate records. Rejected.
- Dedup before blob upload: Content hash isn't available until after upload. Not possible.

---

## R4: Python Link Download Tool — Content Hash from Blob Upload

### Question
The link download tool uses `azure-storage-blob` async SDK's `upload_blob()`. How to get Content-MD5 from the upload response?

### Finding
The `azure-storage-blob` SDK does NOT automatically compute or return Content-MD5 on upload. However, it does support:
1. **Setting** `content_md5` via `ContentSettings` (requires pre-computing)
2. **Getting** blob properties after upload via `get_blob_properties()` which returns server-computed MD5

**Decision**: After `upload_blob()`, call `blob_client.get_blob_properties()` and read `properties.content_settings.content_md5`. This is the same conceptual approach as the Logic App HEAD request — get the hash from the blob after upload.

Alternatively, compute MD5 from the downloaded bytes before upload (we already have them in memory). This avoids an extra API call.

**Final decision**: Compute MD5 from in-memory bytes using `hashlib.md5()`. This is simpler (no extra API call), deterministic, and the bytes are already buffered in the `chunks` list. Encode as base64 to match blob storage Content-MD5 format.

**Alternatives Considered**:
- `get_blob_properties()` after upload: Extra API call, adds latency. Rejected in favor of local computation.
- Request server to compute MD5: Blob storage computes MD5 only if `content_md5` is set on upload. We'd need to pre-compute anyway. Rejected.

---

## R5: Dashboard — Badge Rendering Guard Change

### Question
What's the minimal change to show badges for all intake sources?

### Finding
Current code in [dashboard.html](../../src/webapp/templates/dashboard.html) line ~219:
```html
{% if email.intakeSource == 'sftp' %}
```

This only shows badges for SFTP records. The badge rendering logic inside the block is generic — it reads `email.version` and `email.deliveryCount` and formats badges.

**Decision**: Change the guard to:
```html
{% if email.version is defined and email.version is not none %}
```

This makes badges render for ANY record that has delivery tracking fields populated, regardless of intake source. Legacy records without these fields continue to show "—".

**Alternatives Considered**:
- `{% if email.intakeSource in ['sftp', 'email'] %}`: Would still exclude future intake sources. Rejected.
- Remove the guard entirely: Would cause template errors on records without `version` field. Rejected.

---

## R6: Dashboard Query — Do Delivery Tracking Fields Come Through?

### Question
Does the dashboard's Cosmos query return delivery tracking fields for email records?

### Finding
The dashboard uses `SELECT * FROM c` with `enable_cross_partition_query=True`. Since it selects all fields, any newly populated fields (`contentHash`, `version`, `deliveryCount`, `deliveryHistory`, `lastDeliveredAt`) will automatically be included in the response. No query change needed.

**Decision**: No change to `main.py` query. Verify in testing that the fields are present in the response.

**Alternatives Considered**:
- Add explicit field selection: Would need maintenance. `SELECT *` is already used and sufficient. Rejected.
