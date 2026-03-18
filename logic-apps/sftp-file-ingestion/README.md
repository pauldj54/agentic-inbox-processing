# SFTP File Ingestion Logic App

Consumption-tier Logic App that polls an SFTP server for new files, parses filename metadata, uploads to blob storage, performs content hash dedup with 3-way routing (new/duplicate/update), and routes by file type.

## Trigger

**When_files_are_added_or_modified** — SFTP-SSH polling trigger.

| Setting | Value |
|---|---|
| Connector | `sftpwithssh` |
| Folder | `sftpFolderPath` parameter (default: `/in/`) |
| Poll interval | Every 1 minute |
| Split on | Each file triggers an independent run |

## Workflow Actions (15 Steps)

| # | Action | Description |
|---|--------|-------------|
| 1 | **Get_file_content** | Downloads file content from the SFTP server |
| 2 | **Generate_file_id** | Creates unique ID: `sftp-{guid}` (used for blob path only) |
| 3 | **Parse_file_extension** | Extracts lowercase file extension |
| 4 | **Strip_file_extension** | Removes extension to get base filename |
| 5 | **Parse_filename_parts** | Splits base filename by delimiter into metadata array |
| 6 | **Upload_to_blob** | Uploads file to Azure Blob at `/attachments/{fileId}/{filename}`. |
| 7 | **Get_blob_md5** | HTTP HEAD to Blob REST API to retrieve `Content-MD5` (not returned by managed connector). |
| 8 | **Compute_dedup_key** | `base64(sftpPath)` — used as Cosmos DB document ID for O(1) point-reads |
| 9 | **Check_for_duplicate** | Cosmos DB point-read by dedup key with partition `{sftpUsername}_{YYYY-MM}` |
| 10 | **Handle_duplicate_check** | 3-way routing based on duplicate check result (see below) |
| 11 | **Create_intake_record_if_new** | Creates Cosmos DB record with `version: 1`, `deliveryCount: 1`, `contentHash` (only for new files; skipped for updates) |
| 12 | **Check_if_spreadsheet / Check_if_PDF** | Routes by file extension to SharePoint or Service Bus |
| 13 | **Copy_processed_file_to_processed** | Copies file from `/in/` to `/processed/` (archive) |
| 14 | **Delete_file** | Deletes original file from `/in/` |
| 15 | **Terminate_success** | Overrides run status to Succeeded (needed because step 9 returns 404/Failed for new files) |

## 3-Way Dedup Routing

Duplicate detection uses a Cosmos DB **point-read** where the document ID is `base64(sftpPath)` and the partition key is `{sftpUsername}_{YYYY-MM}`. Dedup is scoped to the same month — cross-month re-deliveries are treated as new files.

| Check Result | Content Hash | Path | Behavior |
|---|---|---|---|
| 404 (not found) | — | **New file** | Create Cosmos record (`version: 1`, `deliveryCount: 1`). Continue to downstream processing. |
| 200 (found) | Same as stored | **True duplicate** | Increment `deliveryCount`, append `deliveryHistory`, update `lastDeliveredAt`. Terminate with `Cancelled` status. |
| 200 (found) | Different from stored | **Content update** | Update `contentHash`, increment `version` + `deliveryCount`, append `deliveryHistory`. Continue to downstream processing (re-upload to SharePoint). |
| Other error | — | **Error** | Terminate with `Failed` status. |

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
| `pdf` | Service Bus | Send to `email-intake` queue (includes `contentHash`, `fileSize`) → Update Cosmos → Archive on SFTP |
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

## Error Handling

| Error Scenario | Behavior |
|---|---|
| True duplicate (same path + same content hash) | `deliveryCount` incremented, run terminated as `Cancelled`. File archived. |
| Content update (same path + different hash) | Record updated with new hash and incremented `version`. File re-processed and re-uploaded. |
| Unexpected Cosmos error (non-404, non-200) | Run terminated as `Failed`. |
| SFTP connection failure | Logic App retries per connector policy. |
| Blob upload failure | Run fails. File stays on SFTP for next poll. |
| Cosmos DB write failure | Run fails. File stays on SFTP for next poll. |
| SharePoint upload failure | Run fails. File stays on SFTP for next poll. |
| Service Bus send failure | Run fails. File stays on SFTP for next poll. |

## Logic App Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `sftpFolderPath` | String | `/in/` | SFTP folder to poll for new files |
| `sftpArchivePath` | String | `/processed/` | SFTP folder to move processed files to |
| `sftpUsername` | String | `partnerreader` | SFTP username, used in partition key computation |
| `cosmosDbAccountName` | String | — | Cosmos DB account name |
| `cosmosDbDatabaseName` | String | `email-processing` | Cosmos DB database name |
| `storageAccountName` | String | `stdocprocdevizr2ch55` | Azure Storage account name |
| `filenameDelimiter` | String | `_` | Delimiter for splitting filename into metadata segments |

## API Connections

| Connection | Auth | Purpose |
|---|---|---|
| `sftpwithssh` | SSH private key (from Key Vault) | SFTP server access |
| `HTTP` (Graph API) | `ActiveDirectoryOAuth` (client credentials) | SharePoint file upload via Microsoft Graph |
| `documentdb` | Managed Identity | Cosmos DB reads/writes |
| `azureblob` | Managed Identity | Azure Blob Storage upload |
| `servicebus` | Managed Identity | Service Bus queue send |
