# SFTP File Ingestion Logic App

Consumption-tier Logic App that polls an SFTP server for new files, parses filename metadata, uploads to blob storage, performs content hash dedup with 3-way routing (new/duplicate/update), routes by file type, and performs outcome-based file disposition — successfully processed files move to `/processed/`, failed files move to `/failed/`.

## Trigger

**When_files_are_added_or_modified** — SFTP-SSH polling trigger.

| Setting | Value |
|---|---|
| Connector | `sftpwithssh` |
| Folder | `sftpFolderPath` parameter (default: `/in/`) |
| Poll interval | Every 1 minute |
| Split on | Each file triggers an independent run |

## Workflow Structure

The workflow uses two **Scope** actions for consolidated error handling:

- **Scope_Early_Processing** — wraps steps 1–8 (file download through dedup key computation)
- **Scope_Route_File** — wraps steps 11–12 (record creation and file type routing)

`Handle_duplicate_check` sits between the two scopes because it contains `Terminate` actions (which would kill the entire run inside a Scope).

### Actions Inside Scope_Early_Processing

| # | Action | Description |
|---|--------|-------------|
| 1 | **Get_file_content** | Downloads file content from the SFTP server |
| 2 | **Generate_file_id** | Creates unique ID: `sftp-{guid}` (used for blob path only) |
| 3 | **Parse_file_extension** | Extracts lowercase file extension |
| 4 | **Strip_file_extension** | Removes extension to get base filename |
| 5 | **Parse_filename_parts** | Splits base filename by delimiter into metadata array |
| 6 | **Upload_to_blob** | Uploads file to Azure Blob at `/attachments/{fileId}/{filename}` |
| 7 | **Get_blob_md5** | HTTP HEAD to Blob REST API to retrieve `Content-MD5` |
| 8 | **Compute_dedup_key** | `base64(sftpPath)` — used as Cosmos DB document ID |

### Dedup + Routing (Between Scopes)

| # | Action | Description |
|---|--------|-------------|
| 9 | **Check_for_duplicate** | Cosmos DB point-read by dedup key |
| 10 | **Handle_duplicate_check** | 3-way routing based on duplicate check result (see below) |

### Actions Inside Scope_Route_File

| # | Action | Description |
|---|--------|-------------|
| 11 | **Create_intake_record_if_new** | Creates Cosmos DB record (only for new files; skipped for updates) |
| 12 | **Check_if_spreadsheet / Check_if_PDF** | Routes by file extension to SharePoint or Service Bus |

### Disposition Paths (After Scope_Route_File)

| Path | Trigger | Actions |
|------|---------|---------|
| **Success** | Scope_Route_File [Succeeded] + supported type | Check_if_supported_type → Copy_to_processed → Update_Cosmos_processed → Delete_from_in → Terminate_success |
| **Unsupported** | Scope_Route_File [Succeeded] + unsupported type | Check_if_supported_type → Delete_unsupported_from_in → Terminate_skipped |
| **Downstream failure** | Scope_Route_File [Failed, TimedOut] | Copy_to_failed → Filter_failed_actions → Update_Cosmos_failed → Delete_from_in_on_failure → Terminate_failed |
| **Duplicate** | Same content hash in Handle_duplicate_check | Copy_dup_to_processed → Delete_dup_from_in → Terminate_duplicate |
| **Dedup error** | Non-404 error in Handle_duplicate_check | Copy_err_to_failed → Delete_err_from_in → Terminate_unexpected_error |
| **Early failure** | Scope_Early_Processing [Failed, TimedOut] | Copy_early_to_failed → Delete_early_from_in → Terminate_early_failed |

## 3-Way Dedup Routing

Duplicate detection uses a Cosmos DB **point-read** where the document ID is `base64(sftpPath)` and the partition key is `{sftpUsername}_{YYYY-MM}`. Dedup is scoped to the same month — cross-month re-deliveries are treated as new files.

| Check Result | Content Hash | Path | Behavior |
|---|---|---|---|
| 404 (not found) | — | **New file** | Create Cosmos record (`version: 1`, `deliveryCount: 1`). Continue to downstream processing. |
| 200 (found) | Same as stored | **True duplicate** | Increment `deliveryCount`, append `deliveryHistory`, update `lastDeliveredAt`. Copy file to `/processed/`. Delete from `/in/`. Terminate with `Cancelled` status. |
| 200 (found) | Different from stored | **Content update** | Update `contentHash`, increment `version` + `deliveryCount`, append `deliveryHistory`. Continue to downstream processing (re-upload to SharePoint). |
| Other error | — | **Error** | Copy file to `/failed/`. Delete from `/in/`. Terminate with `Failed` status. |

Content hash source: `outputs('Get_blob_md5')['headers']['Content-MD5']` — retrieved via HTTP HEAD to Azure Blob REST API with managed identity. The managed blob connector's Create blob response does not include `ContentMD5`.

## Delivery Tracking Fields

| Field | Type | Description |
|---|---|---|
| `contentHash` | string | MD5 hash of file content from blob upload |
| `version` | number | Document version, starts at 1. Incremented on content updates. |
| `deliveryCount` | number | Total times this file path was delivered (including duplicates). |
| `deliveryHistory` | object[] | Array of `{deliveredAt, contentHash, action}` entries. |
| `lastDeliveredAt` | string (ISO 8601) | Timestamp of most recent delivery. |

## Partition Key

Container: `intake-records`, partition key: `/partitionKey`

SFTP records use `{sftpUsername}_{YYYY-MM}` (e.g., `partnerreader_2026-03`). This value is immutable once set and enables efficient point-reads within each partition.

## File Type Routing

| Extension | Route | Actions |
|-----------|-------|---------|
| `csv`, `xlsx`, `xls` | SharePoint | Upload via Graph API HTTP → Update Cosmos (status: `archived`, `sharepointPath` set) → Archive on SFTP |
| `pdf` | Service Bus | Send to `intake` queue (includes `contentHash`, `fileSize`) → Update Cosmos → Archive on SFTP |
| Other | Skip | Logged as unsupported. File stays on SFTP. |

## SharePoint Folder Path Convention

```
{sharepointDocLibraryPath}/{first letter of Account}/{Account}/{Fund}/{filename}
```

Example: `Documents/H/HorizonCapital/GrowthFundIII/HorizonCapital_GrowthFundIII_NAVReport_Q1_20260309_20260301.csv`

SharePoint upload uses **HTTP connector with Graph API** (`ActiveDirectoryOAuth` client credentials), not the managed SharePoint connector.

## Filename Metadata Parsing

**Convention**: `{Account}_{Fund}_{DocType}_{DocName}_{PublishedDate}_{EffectiveDate}.{ext}`

| Position | Field | Example |
|----------|-------|---------|
| 0 | `account` | HorizonCapital |
| 1 | `fund` | GrowthFundIII |
| 2 | `docType` | NAVReport |
| 3 | `docName` | MarchNAV |
| 4 | `publishedDate` | 20260309 → `2026-03-09` |
| 5 | `effectiveDate` | 20260301 → `2026-03-01` |

Date fields are converted from `YYYYMMDD` to ISO 8601 (`YYYY-MM-DD`).

## Error Handling & File Disposition

All files are moved out of `/in/` regardless of outcome — the SFTP trigger uses a watermark model that advances past each file and never re-triggers it.

| Error Scenario | Disposition | Cosmos DB Update |
|---|---|---|
| Successful processing (supported type) | File → `/processed/` | `disposition: "processed"` |
| Unsupported file type | File deleted from `/in/` | No disposition field (no change) |
| True duplicate (same content hash) | File → `/processed/` | `deliveryCount` incremented |
| Content update (different hash) | File → `/processed/` (after re-processing) | `disposition: "processed"` |
| Downstream failure (Scope_Route_File error) | File → `/failed/` | `status: "error"`, `disposition: "failed"`, `errorDetails` |
| Dedup error (non-404, non-200) | File → `/failed/` | No Cosmos update (dedup key exists but record may not) |
| Early failure (Scope_Early_Processing error) | File → `/failed/` | **No Cosmos update** — metadata not yet computed |
| SFTP connection failure | Logic App retries per connector policy | None |

### SFTP Folder Structure

| Folder | Purpose |
|---|---|
| `/in/` | Incoming files (trigger source) — should be empty after processing |
| `/processed/` | Successfully processed files (including duplicates) |
| `/failed/` | Files that encountered processing errors |

### Early Failure Note

If a failure occurs before `Compute_dedup_key` (e.g., blob upload fails), there is no Cosmos DB document ID or partition key to update. The file in `/failed/` is the sole indicator of the failure. Recovery: copy the file from `/failed/` back to `/in/`.

## Logic App Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `sftpFolderPath` | String | `/in/` | SFTP folder to poll for new files |
| `sftpArchivePath` | String | `/processed/` | SFTP folder to move processed files to |
| `sftpFailedPath` | String | `/failed/` | SFTP folder to move failed files to |
| `sftpUsername` | String | `partnerreader` | SFTP username, used in partition key computation |
| `cosmosDbAccountName` | String | — | Cosmos DB account name |
| `cosmosDbDatabaseName` | String | `email-processing` | Cosmos DB database name |
| `storageAccountName` | String | `stdocprocdevizr2ch55` | Azure Storage account name |
| `filenameDelimiter` | String | `_` | Delimiter for splitting filename into metadata segments |

## API Connections

| Connection | Auth | Purpose |
|---|---|---|
| `sftpwithssh` | SSH private key (from Key Vault) | SFTP server access (trigger) |
| `sftpwithssh-1` | SSH private key (from Key Vault) | SFTP file copy/delete operations |
| `HTTP` (Graph API) | `ActiveDirectoryOAuth` (client credentials) | SharePoint file upload via Microsoft Graph |
| `documentdb` | Managed Identity | Cosmos DB reads/writes |
| `azureblob` | Managed Identity | Azure Blob Storage upload |
| `servicebus` | Managed Identity | Service Bus queue send |
