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
**Partition key**: `/status`  
**Producers**: Logic App (initial upsert), Python agent (classification updates)

### New Document Type: SFTP Record

The `intake-records` container now stores two document types, distinguished by `intakeSource`.

| Document Type | `intakeSource` | Producer | Key Fields |
|---|---|---|---|
| Email record | `"email"` | Email ingestion Logic App + agent | `emailId`, `from`, `subject`, `emailBody`, `attachmentPaths` |
| SFTP record | `"sftp"` | SFTP ingestion Logic App + agent | `fileId`/`id`, `originalFilename`, `fileType`, `sftpPath`, `blobPath`, `contentHash`, `account`, `fund`, `docType`, `docName`, `publishedDate`, `effectiveDate`, `sharepointPath` |

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

| Step | Action | Input | Output |
|---|---|---|---|
| 1 | Trigger: detect new file | SFTP folder path | File metadata (name, path, size, lastModified) |
| 2 | Get file content | File path from trigger | File binary content |
| 3 | Compute content hash | File content | MD5 hash string |
| 4 | Check for duplicate | `sftpPath` + `contentHash` → Cosmos DB query | Boolean (duplicate found) |
| 5 | Parse filename metadata | Filename with configurable delimiter (default: `_`) | `account`, `fund`, `docType`, `docName`, `publishedDate`, `effectiveDate` |
| 6 | Upload to blob | File content → `/attachments/sftp-{fileId}/{filename}` | Blob URL |
| 7 | Create Cosmos DB record | File metadata + blob path + parsed metadata | Cosmos DB document |
| 8 | Determine routing | File extension | `"sharepoint"` or `"email-intake"` |
| 9a | (CSV/Excel) Upload to SharePoint | File content → `{root}/{letter}/{account}/{fund}/{filename}` | SharePoint file URL |
| 9b | (PDF) Send to Service Bus | SFTP intake message → `email-intake` queue | Message ID |
| 10 | Update Cosmos DB | Set `sharepointPath` (CSV/Excel) or `queue` (PDF) | Updated document |
| 11 | Move file to archive | Source path → `/processed/{filename}` | Success/failure |

### Duplicate Detection Query

```sql
SELECT VALUE COUNT(1) FROM c 
WHERE c.sftpPath = @sftpPath 
  AND c.contentHash = @contentHash 
  AND c.intakeSource = 'sftp'
```

If count > 0, skip steps 5–11 and log a warning.

### Filename Metadata Parsing

Expected convention: `{Account}_{Fund}_{DocType}_{DocName}_{PublishedDate}_{EffectiveDate}.{ext}`

- Delimiter: configurable (default: `_`)
- Date format: `YYYYMMDD` → converted to ISO 8601
- If parsing fails (wrong number of segments or invalid dates): Cosmos DB record created with `status: "error"`, `metadataParseError` set, no SharePoint upload, file NOT moved to `/processed/`

### SharePoint Folder Path Convention

```
{sharepointSiteUrl}/{sharepointDocLibraryPath}/{first letter of Account}/{Account}/{Fund}/{filename}
```

Example: `https://contoso.sharepoint.com/sites/pe-docs/Documents/H/HorizonCapital/GrowthFundIII/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv`

Folders are created automatically if they do not exist (SharePoint connector handles nested folder creation).

### Error Handling

| Failure Point | Behavior |
|---|---|
| SFTP connection failure | Logic App retry policy (3 retries, exponential backoff). File stays on SFTP. |
| Blob upload failure | Abort processing. File stays on SFTP. Error logged. |
| Cosmos DB write failure | Abort processing. Blob may be orphaned (acceptable — cleanup script). File stays on SFTP. |
| Filename parsing failure | Cosmos DB record created with `status: "error"`, `metadataParseError` set. No SharePoint upload, no Service Bus message. File stays on SFTP. |
| SharePoint upload failure | Cosmos DB record exists with `status: "error"`. Blob backup exists. File stays on SFTP. |
| Service Bus send failure (PDF) | Cosmos DB record exists with `status: "error"`. File stays on SFTP. |
| Archive move failure | Non-fatal. File may be reprocessed; dedup query prevents duplicate processing. |

### Logic App Parameters

> **Note**: SFTP connection credentials (host, port, username, SSH private key) and SharePoint credentials (client ID, client secret, tenant ID) are provisioned at the **infrastructure level** via Bicep API Connection resources using Key Vault `getSecret()` references. They are NOT Logic App workflow parameters. See [research.md §2 and §9](../research.md) for provisioning details.

| Parameter | Type | Description | Example |
|---|---|---|---|
| `sftpFolderPath` | `string` | Monitored folder on SFTP server | `/inbox/` |
| `sftpArchivePath` | `string` | Archive folder on SFTP server | `/processed/` |
| `cosmosDbAccountName` | `string` | Cosmos DB account | `cosmos-docproc-dev` |
| `cosmosDbDatabaseName` | `string` | Cosmos DB database | `email-processing` |
| `serviceBusNamespace` | `string` | Service Bus namespace | `sb-docproc-dev` |
| `sharepointSiteUrl` | `string` | SharePoint Online site URL | `https://contoso.sharepoint.com/sites/pe-docs` |
| `sharepointDocLibraryPath` | `string` | Root document library path | `Documents` |
| `filenameDelimiter` | `string` | Delimiter for filename metadata parsing | `_` |

### API Connection Resources (Bicep-provisioned)

These `Microsoft.Web/connections` resources are provisioned by Bicep and referenced by the Logic App via `$connections` parameter:

| Connection | Managed API | Auth Method | Key Vault Secret |
|---|---|---|---|
| `sftpwithssh` | `sftpwithssh` | SSH private key | `sftp-private-key` |
| `sharepointonline` | `sharepointonline` | Entra ID app (client credentials) | `sharepoint-client-secret` |
| `documentdb` | `documentdb` | Managed Identity | — |
| `azureblob` | `azureblob` | Managed Identity | — |
| `servicebus` | `servicebus` | Managed Identity | — |

---

## 4. Agent Processing Contract: SFTP PDF Files

**Module**: `src/agents/email_classifier_agent.py`  
**Method**: `process_next_email()` (adapted)

### SFTP-Source Branching

When the agent receives an SFTP-sourced message from the `email-intake` queue:

| Processing Step | Email Behavior | SFTP Behavior |
|---|---|---|
| Parse message | Read `emailId`, `from`, `subject`, `bodyText` | Read `fileId`, `originalFilename`, `blobPath` |
| Fetch Cosmos record | Query by `emailId` | Query by `fileId` (same field: `id`) |
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
