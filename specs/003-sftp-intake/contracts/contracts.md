# Contracts: SFTP File Intake Channel

**Feature**: 003-sftp-intake  
**Date**: 2026-03-09

## 1. Service Bus Message Contract: SFTP-Sourced PDF File (New)

**Queue**: `email-intake` (PDF files only)
**Producer**: Logic App (`sftp-file-ingestion` workflow)  
**Consumer**: Python classifier agent (PDF)

> **Note**: CSV/Excel files do NOT produce Service Bus messages. They are uploaded directly to SharePoint by the Logic App.

### Message Schema

```json
{
  "fileId": "string (UUID, e.g., 'sftp-a1b2c3d4-e5f6-7890-abcd-ef1234567890')",
  "originalFilename": "string (e.g., 'HorizonCapital_GrowthFundIII_CapitalCall_Q4Report_20260309_20260301.pdf')",
  "fileType": "string ('pdf')",
  "blobPath": "string ('/attachments/sftp-{fileId}/{filename}')",
  "intakeSource": "string (always 'sftp')",
  "receivedAt": "string (ISO 8601)",
  "sftpPath": "string (original SFTP path)",
  "contentHash": "string (MD5 hash of file content)",
  "fileSize": "integer (bytes)",
  "account": "string (PE fund company, parsed from filename)",
  "fund": "string (PE fund name, parsed from filename)",
  "docType": "string (document type, parsed from filename)",
  "docName": "string (document name, parsed from filename)",
  "publishedDate": "string (ISO 8601, parsed from filename)",
  "effectiveDate": "string (ISO 8601, parsed from filename)"
}
```

### Routing Rules

| File Type | Destination | Processing |
|---|---|---|
| `.csv` | SharePoint (direct, no Service Bus) | Filename metadata parsed → SharePoint upload |
| `.xlsx`, `.xls` | SharePoint (direct, no Service Bus) | Filename metadata parsed → SharePoint upload |
| `.pdf` | `email-intake` queue → agent | Classification (full) or triage-only |

### Key Differences from Email Intake Messages

| Aspect | Email Message | SFTP Message |
|---|---|---|
| ID field | `emailId` (Graph API ID) | `fileId` (UUID) |
| Source indicator | `source: "logic-app-ingestion"` | `intakeSource: "sftp"` |
| Sender info | `from`, `subject`, `bodyText` | `originalFilename`, `sftpPath` |
| Attachments | `attachmentPaths` (array of objects) | `blobPath` (single string) |
| Content hash | Not present | `contentHash` (MD5) |
| File type | Inferred from attachment | `fileType` (explicit) |

### Consumer Detection

The Python agent detects SFTP-sourced messages by checking:

```python
intake_source = message_data.get("intakeSource")
if intake_source == "sftp":
    # SFTP processing path
    file_id = message_data.get("fileId")
    blob_path = message_data.get("blobPath")
else:
    # Email processing path (existing)
    email_id = message_data.get("emailId")
```

---

## 2. Cosmos DB Intake Record Contract (Updated)

**Container**: `intake-records` (renamed from `emails`)  
**Partition key**: `/partitionKey`  
**Producers**: Logic App (initial upsert), Python agent (classification updates)

### New Document Type: SFTP Record

The `intake-records` container now stores two document types, distinguished by `intakeSource`.

| Document Type | `intakeSource` | Producer | Key Fields |
|---|---|---|---|
| Email record | `"email"` | Email ingestion Logic App + agent | `emailId`, `from`, `subject`, `emailBody`, `attachmentPaths` |
| SFTP record | `"sftp"` | SFTP ingestion Logic App + agent | `id` (`base64(sftpPath)` — dedup key), `fileId` (`sftp-{guid}` — blob path only), `originalFilename`, `fileType`, `sftpPath`, `blobPath`, `contentHash`, `version`, `deliveryCount`, `account`, `fund`, `docType`, `docName`, `publishedDate`, `effectiveDate`, `sharepointPath` |

### Backward Compatibility

- Legacy email records without `intakeSource` are treated as `intakeSource: "email"` by the dashboard and agent.
- After migration, all records will have the `intakeSource` field.
- The `emailId` field remains on email records for backward compatibility with existing integrations.

See [data-model.md](../data-model.md) for complete field definitions and examples.

---

## 3. SFTP Logic App Workflow Contract (New)

**Resource**: Logic App `sftp-file-ingestion` workflow  
**Trigger**: SFTP-SSH "When files are added or modified"  
**Produces**: Cosmos DB record + Blob Storage file + Service Bus message (PDF only) + SharePoint upload (CSV/Excel only)

### Workflow Actions (Ordered)

| Step | Action Name | Type | Input | Output |
|---|---|---|---|---|
| 1 | Trigger: `When_files_are_added_or_modified` | ApiConnection (SFTP) | SFTP folder path (folderId `L2lu` = `/in`) | File metadata in headers (`x-ms-file-id`, `x-ms-file-name`, `x-ms-file-path`, `x-ms-file-etag`) |
| 2 | `Get_file_content` | ApiConnection (SFTP) | File ID from trigger headers | File binary content |
| 3 | `Generate_file_id` | Compose | — | `sftp-{guid()}` (used for blob path only, NOT Cosmos doc id) |
| 4 | `Parse_file_extension` | Compose | `toLower(last(split(name, '.')))` | File extension string |
| 5 | `Strip_file_extension` | Compose | Remove extension from filename | Base filename |
| 6 | `Parse_filename_parts` | Compose | Split base filename by `filenameDelimiter` | Array: `[Account, Fund, DocType, DocName, PublishedDate, EffectiveDate]` |
| 7 | `Upload_to_blob` | ApiConnection (Blob) | File content → `/attachments/{fileId}/{filename}` | Blob reference + `ContentMD5` |
| 8 | `Compute_dedup_key` | Compose | `base64(filePath)` | Base64 string, used as Cosmos document ID |
| 9 | `Check_for_duplicate` | ApiConnection (Cosmos DB) | Point-read by dedup key (= doc id), partition = `{sftpUsername}_{YYYY-MM}` (computed at runtime) | 200 = existing record found, 404 = new file |
| 9a | `Handle_duplicate_check` | If (3-way) | Succeeded → compare contentHash (same = dup, different = update); Failed 404 → new file; other → error | Routes to dup/update/new/error paths |
| 9b | (Duplicate) `Patch_delivery_count` | ApiConnection (Cosmos DB) | Increment `deliveryCount`, append `deliveryHistory`, update `lastDeliveredAt` | — |
| 9c | (Duplicate) `Terminate_duplicate` | Terminate | `runStatus: Cancelled` | Stops workflow |
| 9d | (Update) `Patch_content_update` | ApiConnection (Cosmos DB) | Update `contentHash`, increment `version` + `deliveryCount`, append `deliveryHistory` | — |
| 10 | `Create_intake_record` | ApiConnection (Cosmos DB) | File metadata + blob path + parsed metadata + contentHash, `version: 1`, `deliveryCount: 1`, doc id = dedup key, `partitionKey` = `{sftpUsername}_{YYYY-MM}` | Cosmos DB document |
| 11 | `Check_if_spreadsheet` → `Check_if_PDF` | If (nested) | File extension | Routes to CSV/XLSX/PDF/default branches |
| 11a | (CSV/Excel) `Upload_to_SharePoint` | HTTP (Graph API) | PUT to `drives/{driveId}/root:/{letter}/{account}/{fund}/{filename}:/content` | SharePoint file |
| 11b | (PDF) `Compose_Service_Bus_Message` + `Send_to_Service_Bus` | Compose + ApiConnection (SB) | SFTP intake message → `email-intake` queue | Message ID |
| 11c | (Default) `Terminate_unsupported` | Terminate | `runStatus: Failed`, unsupported file type | — |
| 12 | `Copy_processed_file_to_processed` | ApiConnection (SFTP) | Copy file from `/in/` to `/processed/` | — |
| 13 | `Delete_file` | ApiConnection (SFTP) | Delete original file from `/in/` | — |
| 14 | `Terminate_success` | Terminate | `runStatus: Succeeded` | Overrides run status (needed because step 9 returns 404/Failed for new files) |

### Duplicate Detection (Point-Read with Content Hash)

Duplicate detection uses a Cosmos DB **point-read** (GET by document ID) where the document ID is the dedup key `base64(sftpPath)`. The partition key for SFTP records is `{sftpUsername}_{YYYY-MM}` (computed from the SFTP connection username parameter + current year-month). Note: dedup is scoped to the same month; cross-month re-deliveries are treated as new files.

**3-way routing after point-read:**

- **404 (Failed action)**: Document not found → **new file**. Create Cosmos record with `version: 1`, `deliveryCount: 1`. Continue to downstream processing.
- **200 (Succeeded) + same `contentHash`**: **True duplicate**. Patch: increment `deliveryCount`, append `deliveryHistory` entry with `action: "duplicate"`. Terminate with `Cancelled` status. Skip all downstream processing.
- **200 (Succeeded) + different `contentHash`**: **Content update**. Patch: update `contentHash`, increment `version` + `deliveryCount`, append `deliveryHistory` entry with `action: "update"`, update `lastDeliveredAt`. Continue to downstream processing (re-upload to SharePoint / re-queue to Service Bus).
- **Other error**: Unexpected failure. Terminate with `Failed` status and error details.

> **Note**: The blob upload happens BEFORE the dedup check so that `body('Upload_to_blob')?['ContentMD5']` is available for content hash comparison. This means duplicate deliveries still create a blob (which is acceptable — the blob can be cleaned up or serves as an audit trail).

### Filename Metadata Parsing

Expected convention: `{Account}_{Fund}_{DocType}_{DocName}_{PublishedDate}_{EffectiveDate}.{ext}`

- Delimiter: configurable (default: `_`)
- Date format: `YYYYMMDD` → converted to ISO 8601
- If parsing fails (wrong number of segments or invalid dates): Cosmos DB record created with `status: "error"`, `metadataParseError` set, no SharePoint upload, file NOT moved to `/processed/`

### SharePoint Upload (Graph API)

SharePoint uploads use **HTTP actions with Graph API** (not the SharePoint managed connector, which doesn't support service principal auth in Consumption tier). The upload uses `PUT /drives/{driveId}/root:/{path}:/content` with `ActiveDirectoryOAuth` authentication.

**Folder path convention**:
```
{first letter of Account}/{Account}/{Fund}/{filename}
```

**Graph API URL**:
```
https://graph.microsoft.com/v1.0/drives/{sharepointDriveId}/root:/{letter}/{Account}/{Fund}/{filename}:/content
```

Example path: `/H/HorizonCapital/GrowthFundIII/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv`

Folders are created automatically by Graph API on PUT if they do not exist.

### Error Handling

| Failure Point | Behavior |
|---|---|
| SFTP connection failure | Logic App retry policy (3 retries, exponential backoff). File stays on SFTP. |
| Blob upload failure | Abort processing. File stays on SFTP. Error logged. |
| Cosmos DB write failure | Abort processing. Blob may be orphaned (acceptable — cleanup script). File stays on SFTP. |
| Filename parsing failure | Cosmos DB record created with `status: "error"`, `metadataParseError` set. No SharePoint upload, no Service Bus message. File stays on SFTP. |
| SharePoint upload failure | Cosmos DB record exists with `status: "error"`. Blob backup exists. File stays on SFTP. |
| Service Bus send failure (PDF) | Cosmos DB record exists with `status: "error"`. File stays on SFTP. |
| Archive move failure | Non-fatal. File may be reprocessed; dedup point-read prevents duplicate processing. Content hash comparison distinguishes true duplicates from updates. |

### Logic App Parameters

> **Note**: SFTP connection credentials (host, port, username, SSH private key) are provisioned at the **infrastructure level** via the `sftpwithssh` Bicep API Connection resource using Key Vault `getSecret()` references. SharePoint credentials (client ID, client secret, tenant ID, drive ID) are **Logic App workflow parameters** injected at Bicep deployment time (the SharePoint managed connector is not used). See [research.md §2](../research.md) for SFTP provisioning details.

| Parameter | Type | Description | Example |
|---|---|---|---|
| `sftpFolderPath` | `string` | Monitored folder on SFTP server | `/` |
| `sftpArchivePath` | `string` | Archive folder on SFTP server | `/processed/` |
| `sftpUsername` | `string` | SFTP user for partition key computation | `partnerreader` |
| `cosmosDbAccountName` | `string` | Cosmos DB account | `cosmos-docproc-dev-izr2ch55woa3c` |
| `cosmosDbDatabaseName` | `string` | Cosmos DB database | `email-processing` |
| `storageAccountName` | `string` | Blob storage account for file backups | `stdocprocdevizr2ch55` |
| `filenameDelimiter` | `string` | Delimiter for filename metadata parsing | `_` |
| `sharepointClientId` | `string` | Entra ID app registration for Graph API | `a4ba9c05-...` |
| `sharepointClientSecret` | `string` | Client secret (from Key Vault at deploy time) | |
| `sharepointTenantId` | `string` | Entra ID tenant | `2ce91bb1-...` |
| `sharepointDriveId` | `string` | SharePoint document library drive ID | `b!MszTwW...` |

### API Connection Resources (Bicep-provisioned)

These `Microsoft.Web/connections` resources are provisioned by Bicep and referenced by the Logic App via `$connections` parameter:

| Connection | Managed API | Auth Method | Notes |
|---|---|---|---|
| `sftpwithssh` | `sftpwithssh` | SSH private key (PEM format) | Key Vault `sftp-private-key` |
| `documentdb` | `documentdb` | Managed Identity | |
| `azureblob` | `azureblob` | Managed Identity | |
| `servicebus` | `servicebus` | Managed Identity | Shared with email Logic App |

> **Note**: SharePoint uploads use **HTTP actions with Graph API** and `ActiveDirectoryOAuth`, not the `sharepointonline` managed connector (which doesn't support service principal auth in Consumption tier). The SharePoint credentials (`sharepointClientId`, `sharepointClientSecret`, `sharepointTenantId`, `sharepointDriveId`) are Logic App workflow parameters, not API Connection resources.

---

## 4. Agent Processing Contract: SFTP PDF Files

**Module**: `src/agents/email_classifier_agent.py`  
**Method**: `process_next_email()` (adapted)

### SFTP-Source Branching

When the agent receives an SFTP-sourced message from the `email-intake` queue:

| Processing Step | Email Behavior | SFTP Behavior |
|---|---|---|
| Parse message | Read `emailId`, `from`, `subject`, `bodyText` | Read `fileId`, `originalFilename`, `blobPath` |
| Fetch Cosmos record | Query by `emailId` | Point-read by `base64(sftpPath)` (= document `id`), partition `{sftpUsername}_{YYYY-MM}`. Note: `fileId` (`sftp-{guid}`) is for blob paths only. |
| Step 1: Relevance check | Analyze email body + subject + attachments | Analyze attachment content only (via `blobPath`) |
| Step 1.5: Link download | Scan email body for download links | **Skip** — file already downloaded |
| Step 2: Classification | Full classification with email context | Classification without email context |
| Routing | Based on confidence + relevance | Same logic |

### Prompt Adaptation

For SFTP-sourced PDFs, the classification prompt omits email-specific fields:

```text
# Email context (omitted for SFTP)
# Instead, use:
Source: SFTP file intake
Filename: {originalFilename}
File type: {fileType}
# Attachment content follows...
```

### Return Contract

The agent's Cosmos DB update for SFTP records uses the same `update_email_classification()` method, with `email_data` containing SFTP fields instead of email fields. The method handles both transparently.
