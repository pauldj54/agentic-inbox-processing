# Application Data Management

## Cosmos DB Data Model

**Account:** `cosmos-docproc-dev-izr2ch55woa3c`
**Database:** `email-processing`
**Auth:** `DefaultAzureCredential` (passwordless via Managed Identity / Azure CLI)

### Active Containers

| Container | Partition Key | Purpose |
|---|---|---|
| `intake-records` | `/partitionKey` | Primary store for every ingested email/SFTP file |
| `pe-events` | `/eventType` | Deduplicated private-equity events extracted from documents |
| `audit-logs` | `/action` | Immutable audit trail for every processing step |
| `classifications` | `/eventType` | Classification results (API endpoint, currently empty) |

> **Deleted containers** (no longer referenced in code): `emails`, `fund-mappings`.
> The constant `CONTAINER_EXTRACTED_DATA = "extracted-data"` is defined in code but the container does not exist in the database yet (created on first write in full-pipeline mode).

---

### intake-records

The canonical record for each ingested item. One document per email or SFTP file.

| Field | Type | Description |
|---|---|---|
| `id` | string | Graph API message ID or SFTP dedup key |
| `emailId` | string | Same as `id` (for backward compat) |
| `partitionKey` | string | `{sender_domain}_{YYYY-MM}` (e.g. `microsoft.com_2026-04`) |
| `from` | string | Sender email address |
| `subject` | string | Email subject line |
| `emailBody` | string | Plain-text body |
| `receivedAt` | ISO 8601 | When the email/file was received |
| `intakeSource` | string | `"email"` or `"sftp"` |
| `status` | string | `received` → `triaged` / `classified` / `needs_review` / `discarded` |
| `queue` | string | Target Service Bus queue for downstream routing |
| `pipelineMode` | string | `"triage-only"` or `"full"` |
| `hasAttachments` | bool | Whether attachments are present |
| `attachmentsCount` | int | Number of attachments |
| `attachmentPaths` | array | `[{path, source, contentMd5, originalName}]` |
| `rejectedAttachments` | array | Attachments rejected by the Logic App (wrong type/size) |
| `downloadFailures` | array | Failed link downloads |
| `linkDownload` | object | Metadata from link-download step |
| `contentHash` | string | Base64 MD5 for dedup |
| `deliveryCount` | int | How many times this content was delivered |
| `deliveryHistory` | array | `[{deliveredAt, contentHash, action}]` |
| `version` | int | Incremented on content-changed redelivery |
| `relevanceCheck` | object | `{isRelevant, initialCategory, confidence, reasoning, checkedAt}` |
| `classification` | object | `{category, confidence, fund_name, pe_company, reasoning, key_evidence, amount, due_date, detected_language, classifiedAt}` |
| `stepsExecuted` | array | Pipeline steps that ran (e.g. `["relevance_check", "triage_complete"]`) |
| `processedAt` | ISO 8601 | When final classification was written |
| `createdAt` | ISO 8601 | Document creation time |
| `updatedAt` | ISO 8601 | Last modification time |

---

### pe-events

One document per unique private-equity event. Multiple emails about the same capital call or distribution are linked to a single event via a SHA-256 dedup key.

**Dedup key grain:** `pe_company | fund_name | event_type | amount | due_date_month | investor`

| Field | Type | Description |
|---|---|---|
| `id` | string | `pe-{dedupKey}-{timestamp}` |
| `dedupKey` | string | First 16 chars of SHA-256 hash of the 6-field composite key |
| `eventType` | string | `Capital Call`, `Distribution`, `NAV Statement`, etc. (partition key) |
| `peCompany` | string | PE firm name |
| `fundName` | string | Fund name |
| `investor` | string | Investor / LP name |
| `amount` | string | Transaction amount |
| `dueDate` | string | Due date |
| `emailIds` | array | List of linked email/SFTP record IDs |
| `emailCount` | int | Count of linked source records |
| `sourceRecords` | array | `[{id, source, receivedAt}]` |
| `status` | string | `pending` / `archived` / `reviewed` |
| `confidence` | float | Classification confidence score |
| `reasoning` | string | LLM reasoning text |
| `keyEvidence` | array | Evidence snippets |
| `createdAt` | ISO 8601 | Event creation time |
| `lastEmailAt` | ISO 8601 | Most recent linked email timestamp |
| `updatedAt` | ISO 8601 | Last modification time |

---

### audit-logs

Append-only log of every processing action. Used by the dashboard for timeline views.

| Field | Type | Description |
|---|---|---|
| `id` | string | `{emailId}-{eventType}-{unix_timestamp}` |
| `emailId` | string | Source record ID |
| `action` | string | Event type / partition key (see below) |
| `eventType` | string | Same as `action` |
| `timestamp` | ISO 8601 | When the event occurred |
| `details` | object | Free-form payload (varies by event type) |

**Common `action` values:** `processing_started`, `relevance_check`, `classification_complete`, `link_download_complete`, `attachments_processed`, `pe_event_created`, `triage_complete`, `discarded`.

---

### classifications

Stores classification snapshots. Queried by the `/api/classifications` dashboard endpoint. Currently empty (populated only in `full` pipeline mode).

| Field | Type | Description |
|---|---|---|
| `id` | string | Document ID |
| `eventType` | string | Classification category (partition key) |
| `*` | varies | Schema matches the `classification` sub-object in `intake-records` |

---

## Utility Scripts

All scripts live in the `utils/` directory and load credentials from `.env01` at the repo root.

### Reset & Cleanup

| Script | Purpose | Usage |
|---|---|---|
| `factory_reset.py` | **Full factory reset** — pauses the Email Logic App, SFTP Logic App, and Web App; deletes all documents from `intake-records`, `pe-events`, `audit-logs`, and `classifications`; purges Service Bus queues; clears the `attachments` blob container; then starts the apps again. | `python utils/factory_reset.py --dry-run` (preview) |
| | | `python utils/factory_reset.py` (interactive confirm) |
| | | `python utils/factory_reset.py --yes` (skip prompt) |
| | | `python utils/factory_reset.py --container pe-events` (single container) |
| | | `python utils/factory_reset.py --leave-stopped` (reset data but keep apps stopped) |
| | | `python utils/factory_reset.py --skip-app-control` (do not pause/start apps) |
| | | `python utils/factory_reset.py --skip-storage` (do not clear blobs) |
| `purge_queues.py` | Drain all Service Bus queues (`intake`, `discarded`, `human-review`, `archival-pending`, `triage-complete`). | `python utils/purge_queues.py --dry-run` |
| `cleanup_pe_events.py` | Delete all documents from `pe-events` only. | `python utils/cleanup_pe_events.py` |
| `cleanup_orphans.py` | Remove orphan documents (no `status` field) from the legacy `emails` container. | `python utils/cleanup_orphans.py` |
| `cleanup_sftp_orphans.py` | Remove duplicate SFTP records created by the `fileId` vs `dedupKey` bug. | `python utils/cleanup_sftp_orphans.py --delete` |
| `delete_orphans.py` | Delete documents with null partition key from the legacy `emails` container. | `python utils/delete_orphans.py` |
| `clear_cosmos_emails.py` | Legacy cleanup script (targets the now-deleted `emails` container). | `python utils/clear_cosmos_emails.py --all-containers` |
| `fix_flattened_attachments.py` | Repair `intake-records` where `attachmentPaths` was incorrectly flattened by the regex fallback parser. | `python utils/fix_flattened_attachments.py --apply` |

### Diagnostics & Queries

| Script | Purpose | Usage |
|---|---|---|
| `diagnose.py` | Check Service Bus queue depths and Cosmos DB container counts. | `python utils/diagnose.py` |
| `query_cosmos.py` | Run ad-hoc SQL queries against `intake-records`. | `python utils/query_cosmos.py "SELECT TOP 5 * FROM c"` |
| `check_status.py` | Check processing status of recent intake records. | `python utils/check_status.py` |
| `check_emails.py` | Inspect email records in Cosmos DB. | `python utils/check_emails.py` |
| `check_test_docs.py` | Verify test documents are present in storage. | `python utils/check_test_docs.py` |
| `test_connectivity.py` | Validate connectivity to Cosmos DB, Service Bus, and Storage. | `python utils/test_connectivity.py` |
| `test_graph_api.py` | Test Microsoft Graph API authentication and mailbox access. | `python utils/test_graph_api.py` |

### Testing

| Script | Purpose | Usage |
|---|---|---|
| `send_test_email.py` | Send a test email into the pipeline via Graph API. | `python utils/send_test_email.py` |
| `send_test_triage_message.py` | Enqueue a test message directly onto the `intake` Service Bus queue. | `python utils/send_test_triage_message.py` |

---

## Recommended Factory Reset Procedure

To fully reset the application to a clean state:

```bash
# 1. Preview what will be paused/deleted/restarted
python utils/factory_reset.py --dry-run

# 2. Pause apps, clear Cosmos DB, queues, and blob attachments, then restart apps
python utils/factory_reset.py --yes

# 3. Verify clean state
python utils/diagnose.py
```

After reset, send a new unread test email or SFTP file to verify the pipeline processes correctly and the dashboard populates with fresh data.
