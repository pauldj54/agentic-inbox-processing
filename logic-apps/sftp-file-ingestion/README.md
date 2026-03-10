# SFTP File Ingestion Logic App

Consumption-tier Logic App that polls an SFTP server for new files, parses filename metadata, and routes them based on file type.

## Trigger

**When_files_are_added_or_modified** — SFTP-SSH polling trigger.

| Setting | Value |
|---|---|
| Connector | `sftpwithssh` |
| Folder | `sftpFolderPath` parameter (default: `/inbox/`) |
| Poll interval | Every 1 minute |
| Split on | Each file triggers an independent run |

## Workflow Actions (11 Steps)

| # | Action | Description |
|---|--------|-------------|
| 1 | **Get_file_content** | Downloads file content from the SFTP server |
| 2 | **Compute_content_hash** | MD5 hash of file content for duplicate detection |
| 3 | **Generate_file_id** | Creates unique ID: `sftp-{guid}` |
| 4 | **Check_for_duplicate** | Queries Cosmos DB `intake-records` for matching `sftpPath` + `contentHash` + `intakeSource='sftp'` |
| 5 | **Is_duplicate** | If count > 0 → logs warning, file stays on SFTP (not moved). Otherwise continues. |
| 6 | **Parse_filename_metadata** | Strips extension, splits base name by configurable delimiter (`_`) |
| 7 | **Validate_segment_count** | Expects exactly 6 segments. If mismatch → creates error record with `metadataParseError`, file stays on SFTP. |
| 8 | **Extract_metadata_fields** | Maps positional segments: `account`, `fund`, `docType`, `docName`, `publishedDate` (ISO), `effectiveDate` (ISO) |
| 9 | **Upload_to_blob_storage** | Uploads to Azure Blob at `/attachments/sftp-{fileId}/{filename}` |
| 10 | **Create_Cosmos_DB_record** | Upserts intake record with status `received` and parsed metadata |
| 11 | **Route_by_file_type** | Switch on extension → CSV/XLSX/XLS/PDF/default |

## File Type Routing

| Extension | Route | Actions |
|-----------|-------|---------|
| `csv` | SharePoint | Upload to SharePoint → Update Cosmos (status: `archived`, `sharepointPath` set) → Move to SFTP archive |
| `xlsx` | SharePoint | Same as CSV |
| `xls` | SharePoint | Same as CSV |
| `pdf` | Service Bus | Send to `email-intake` queue → Update Cosmos (queue: `email-intake`) → Move to SFTP archive |
| Other | Skip | Log unsupported file type. File stays on SFTP (not moved). |

## SharePoint Folder Path Convention

```
{sharepointDocLibraryPath}/{first letter of Account}/{Account}/{Fund}/{filename}
```

Example: `Documents/H/HorizonCapital/GrowthFundIII/HorizonCapital_GrowthFundIII_NAVReport_Q1_20260309_20260301.csv`

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

Segment count must be exactly 6. If not, the workflow creates an error record in Cosmos DB with `metadataParseError` and the file remains on SFTP.

## Error Handling

| Error Scenario | Behavior |
|---|---|
| Duplicate file (same `sftpPath` + `contentHash`) | Logged, skipped. File stays on SFTP. |
| Filename parse failure (segment count ≠ 6) | Cosmos error record created. File stays on SFTP. |
| Unsupported file type | Logged, skipped. File stays on SFTP. |
| SFTP connection failure | Logic App retries per connector policy. |
| Blob upload failure | Run fails. File stays on SFTP for next poll. |
| Cosmos DB write failure | Run fails. File stays on SFTP for next poll. |
| SharePoint upload failure | Run fails. File stays on SFTP for next poll. |
| Service Bus send failure | Run fails. File stays on SFTP for next poll. |

## Logic App Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `sftpFolderPath` | String | `/inbox/` | SFTP folder to poll for new files |
| `sftpArchivePath` | String | `/processed/` | SFTP folder to move processed files to |
| `cosmosDbAccountName` | String | — | Cosmos DB account name |
| `cosmosDbDatabaseName` | String | `email-processing` | Cosmos DB database name |
| `storageAccountName` | String | — | Azure Storage account name |
| `sharepointSiteUrl` | String | — | SharePoint site URL |
| `sharepointDocLibraryPath` | String | `Documents` | SharePoint document library root path |
| `filenameDelimiter` | String | `_` | Delimiter for splitting filename into metadata segments |

## API Connections

| Connection | Auth | Purpose |
|---|---|---|
| `sftpwithssh` | SSH private key (from Key Vault) | SFTP server access |
| `sharepointonline` | Entra ID app (client credentials) | SharePoint file upload |
| `documentdb` | Managed Identity | Cosmos DB reads/writes |
| `azureblob` | Managed Identity | Azure Blob Storage upload |
| `servicebus` | Managed Identity | Service Bus queue send |
