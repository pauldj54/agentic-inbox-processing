# Data Model: SFTP File Intake Channel

**Feature**: 003-sftp-intake  
**Date**: 2026-03-09

## Entity Changes

### 1. Intake Record (Cosmos DB — container rename + schema extension)

**Container**: `intake-records` (renamed from `emails`)  
**Partition key**: `/status`  
**Change type**: Breaking — container rename + schema migration

#### Container Rename

The `emails` container is renamed to `intake-records` to reflect its multi-source nature. All existing documents must be migrated, backfilling `intakeSource: "email"` on each.

| Aspect | Before | After |
|---|---|---|
| Container name | `emails` | `intake-records` |
| Code constant | `CONTAINER_EMAILS = "emails"` | `CONTAINER_INTAKE_RECORDS = "intake-records"` |
| Partition key | `/status` | `/status` (unchanged) |
| Indexing policy | Composite indexes on status+receivedAt, confidenceLevel+receivedAt | Same + composite on intakeSource+status |

#### Field: `intakeSource` (new, required)

| Field | Type | Required | Values | Description |
|---|---|---|---|---|
| `intakeSource` | `string` | Yes | `"email"` \| `"sftp"` | Discriminator identifying the intake channel |

**Notes**:
- All existing email records MUST be backfilled with `intakeSource: "email"` during migration.
- New SFTP records are created with `intakeSource: "sftp"` by the SFTP Logic App workflow.
- Used by the dashboard to display source indicators and by the agent to branch processing logic.

#### SFTP-specific fields (new, present only when `intakeSource: "sftp"`)

| Field | Type | Required | Description |
|---|---|---|---|
| `originalFilename` | `string` | Yes | Original filename from the SFTP server (e.g., `"report-q4.pdf"`) |
| `fileType` | `string` | Yes | File extension: `"csv"`, `"xlsx"`, `"xls"`, `"pdf"` |
| `fileSize` | `number` | Yes | File size in bytes |
| `sftpPath` | `string` | Yes | Full path on the SFTP server (e.g., `"/inbox/report-q4.pdf"`) |
| `contentHash` | `string` | Yes | MD5 hash of file content for duplicate detection |
| `account` | `string` | Conditional | PE fund company name (parsed from filename). Present when filename matches convention. |
| `fund` | `string` | Conditional | PE fund name (parsed from filename). Present when filename matches convention. |
| `docType` | `string` | Conditional | Document type (parsed from filename, e.g., `"CapitalCall"`, `"NAVReport"`). Present when filename matches convention. |
| `docName` | `string` | Conditional | Document name (parsed from filename). Present when filename matches convention. |
| `publishedDate` | `string` (ISO 8601) | Conditional | Published date (parsed from filename, YYYYMMDD → ISO 8601). Present when filename matches convention. |
| `effectiveDate` | `string` (ISO 8601) | Conditional | Effective date (parsed from filename, YYYYMMDD → ISO 8601). Present when filename matches convention. |
| `sharepointPath` | `string` | No | SharePoint document path after upload (populated for CSV/Excel files only) |
| `metadataParseError` | `string` | No | Error message if filename metadata parsing failed |

**Notes**:
- Email-specific fields (`from`, `subject`, `emailBody`, `hasAttachments`, `attachmentsCount`, `attachmentPaths`, `downloadFailures`, `linkDownload`) are absent on SFTP records.
- `blobPath` replaces the role of `attachmentPaths` for SFTP records — single file per record vs. multiple attachments per email.
- Parsed metadata fields (`account`, `fund`, `docType`, `docName`, `publishedDate`, `effectiveDate`) are extracted from the filename at ingestion time per the convention `{Account}_{Fund}_{DocType}_{Name}_{PublishedDate}_{EffectiveDate}.{ext}`.
- These metadata fields are **Conditional**: present when the filename matches the naming convention, absent when parsing fails.
- If filename parsing fails, `metadataParseError` captures the error and `status` is set to `"error"`. The metadata fields are omitted.
- `sharepointPath` is populated only for CSV/Excel files after successful SharePoint upload. PDF files do not go to SharePoint.

#### Shared base fields (present on all records)

| Field | Type | Required | Source | Description |
|---|---|---|---|---|
| `id` | `string` | Yes | Both | Unique identifier (email: Graph API message ID, SFTP: generated UUID) |
| `intakeSource` | `string` | Yes | Both | `"email"` or `"sftp"` |
| `status` | `string` | Yes | Both | Partition key. Values: `"received"`, `"processing"`, `"classified"`, `"archived"`, `"discarded"`, `"needs_review"`, `"triaged"`, `"error"` |
| `receivedAt` | `string` (ISO 8601) | Yes | Both | When the record was ingested |
| `processedAt` | `string` (ISO 8601) | No | Both | When processing completed |
| `classification` | `object` | No | Both | Classification result (null for CSV/Excel, null in triage-only mode) |
| `relevanceCheck` | `object` | No | Both | Relevance check result from Step 1 |
| `pipelineMode` | `string` | No | Both | `"full"` or `"triage-only"` |
| `stepsExecuted` | `string[]` | No | Both | Ordered list of processing steps completed |
| `queue` | `string` | No | Both | Destination queue name |
| `createdAt` | `string` (ISO 8601) | No | Both | Document creation timestamp |
| `updatedAt` | `string` (ISO 8601) | No | Both | Last update timestamp |

#### Email-specific fields (present only when `intakeSource: "email"`)

| Field | Type | Required | Description |
|---|---|---|---|
| `emailId` | `string` | Yes | Graph API message ID (same as `id`) |
| `from` | `string` | Yes | Sender email address |
| `subject` | `string` | Yes | Email subject line |
| `emailBody` | `string` | No | Full email body (HTML or plain text) |
| `hasAttachments` | `boolean` | Yes | Whether email has attachments |
| `attachmentsCount` | `number` | Yes | Number of attachments |
| `attachmentPaths` | `object[]` | Yes | Array of `{path, source}` objects (source: `"attachment"` or `"link"`) |
| `downloadFailures` | `object[]` | No | Failed link download attempts |
| `linkDownload` | `object` | No | Link download processing metadata |

#### Full SFTP document example

```json
{
  "id": "sftp-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "intakeSource": "sftp",
  "status": "received",
  "receivedAt": "2026-03-09T14:30:00Z",
  "originalFilename": "HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf",
  "fileType": "pdf",
  "fileSize": 245780,
  "sftpPath": "/inbox/HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf",
  "contentHash": "d41d8cd98f00b204e9800998ecf8427e",
  "blobPath": "/attachments/sftp-a1b2c3d4/HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf",
  "account": "HorizonCapital",
  "fund": "GrowthFundIII",
  "docType": "CapitalCall",
  "docName": "Q4Notice",
  "publishedDate": "2026-03-09",
  "effectiveDate": "2026-04-01",
  "processedAt": null,
  "classification": null,
  "relevanceCheck": null,
  "pipelineMode": null,
  "stepsExecuted": ["metadata-parse"],
  "queue": null,
  "createdAt": "2026-03-09T14:30:00Z",
  "updatedAt": "2026-03-09T14:30:00Z"
}
```

#### Full SFTP document example (after classification — full mode)

```json
{
  "id": "sftp-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "intakeSource": "sftp",
  "status": "classified",
  "receivedAt": "2026-03-09T14:30:00Z",
  "originalFilename": "HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf",
  "fileType": "pdf",
  "fileSize": 245780,
  "sftpPath": "/inbox/HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf",
  "contentHash": "d41d8cd98f00b204e9800998ecf8427e",
  "blobPath": "/attachments/sftp-a1b2c3d4/HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf",
  "account": "HorizonCapital",
  "fund": "GrowthFundIII",
  "docType": "CapitalCall",
  "docName": "Q4Notice",
  "publishedDate": "2026-03-09",
  "effectiveDate": "2026-04-01",
  "processedAt": "2026-03-09T14:32:15Z",
  "classification": {
    "category": "Capital Call",
    "confidence": 0.87,
    "fund_name": "Horizon Growth Fund III",
    "pe_company": "Horizon Capital Partners",
    "reasoning": "Document contains capital call notice with fund name and commitment amount.",
    "key_evidence": ["capital call", "commitment amount", "due date"],
    "amount": "$5,000,000",
    "due_date": "2026-04-01",
    "detected_language": "English",
    "classifiedAt": "2026-03-09T14:32:15Z"
  },
  "relevanceCheck": {
    "isRelevant": true,
    "initialCategory": "Capital Call",
    "confidence": 0.92,
    "reasoning": "PDF attachment contains PE capital call terminology.",
    "checkedAt": "2026-03-09T14:31:45Z"
  },
  "pipelineMode": "full",
  "stepsExecuted": ["triage", "classification", "routing"],
  "queue": "archival-pending",
  "createdAt": "2026-03-09T14:30:00Z",
  "updatedAt": "2026-03-09T14:32:15Z"
}
```

#### Full SFTP document example (CSV — SharePoint archival)

```json
{
  "id": "sftp-b2c3d4e5-f6a7-8901-bcde-f23456789012",
  "intakeSource": "sftp",
  "status": "archived",
  "receivedAt": "2026-03-09T15:00:00Z",
  "originalFilename": "HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv",
  "fileType": "csv",
  "fileSize": 12450,
  "sftpPath": "/inbox/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv",
  "contentHash": "e99a18c428cb38d5f260853678922e03",
  "blobPath": "/attachments/sftp-b2c3d4e5/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv",
  "account": "HorizonCapital",
  "fund": "GrowthFundIII",
  "docType": "NAVReport",
  "docName": "MarchNAV",
  "publishedDate": "2026-03-09",
  "effectiveDate": "2026-03-01",
  "sharepointPath": "https://contoso.sharepoint.com/sites/pe-docs/Documents/H/HorizonCapital/GrowthFundIII/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv",
  "processedAt": "2026-03-09T15:00:45Z",
  "classification": null,
  "relevanceCheck": null,
  "pipelineMode": null,
  "stepsExecuted": ["metadata-parse", "sharepoint-upload"],
  "queue": null,
  "createdAt": "2026-03-09T15:00:00Z",
  "updatedAt": "2026-03-09T15:00:45Z"
}
```

#### Migrated email document example (backfilled with `intakeSource`)

```json
{
  "id": "AAMkADI5...",
  "intakeSource": "email",
  "emailId": "AAMkADI5...",
  "status": "classified",
  "receivedAt": "2026-02-26T10:00:00Z",
  "from": "sender@example.com",
  "subject": "PE Documents - Capital Call",
  "emailBody": "<html>...</html>",
  "hasAttachments": true,
  "attachmentsCount": 2,
  "attachmentPaths": [
    {"path": "AAMkADI5.../invoice.pdf", "source": "attachment"},
    {"path": "AAMkADI5.../report.pdf", "source": "link"}
  ],
  "downloadFailures": [],
  "linkDownload": { "linksFound": 1, "downloaded": 1, "failures": [] },
  "processedAt": "2026-02-26T10:05:00Z",
  "classification": {
    "category": "Capital Call",
    "confidence": 0.85,
    "fund_name": "Horizon Growth Fund III",
    "pe_company": "Horizon Capital Partners",
    "reasoning": "Email contains capital call documents.",
    "key_evidence": ["capital call"],
    "classifiedAt": "2026-02-26T10:05:00Z"
  },
  "relevanceCheck": {
    "isRelevant": true,
    "initialCategory": "Capital Call",
    "confidence": 0.90,
    "reasoning": "Subject contains PE terminology.",
    "checkedAt": "2026-02-26T10:03:00Z"
  },
  "pipelineMode": "full",
  "stepsExecuted": ["triage", "pre-processing", "classification", "routing"],
  "queue": "archival-pending",
  "createdAt": "2026-02-26T10:00:00Z",
  "updatedAt": "2026-02-26T10:05:00Z"
}
```

---

### 2. SFTP Intake Message (Service Bus)

**Queue**: `email-intake` (for PDF files only)  
**Change type**: New message format for SFTP-sourced PDF files

CSV/Excel files from SFTP do NOT use Service Bus. They are uploaded directly to SharePoint by the Logic App.

#### Message schema (SFTP-sourced PDF file — Service Bus)

> **Note**: This schema applies to PDF files only. CSV/Excel files do NOT produce Service Bus messages.

| Field | Type | Required | Description |
|---|---|---|---|
| `fileId` | `string` | Yes | Unique identifier (UUID) for the file |
| `originalFilename` | `string` | Yes | Original filename from SFTP |
| `fileType` | `string` | Yes | File extension: `"pdf"` |
| `blobPath` | `string` | Yes | Blob storage path |
| `intakeSource` | `string` | Yes | Always `"sftp"` |
| `receivedAt` | `string` (ISO 8601) | Yes | When the file was detected |
| `sftpPath` | `string` | Yes | Original path on SFTP server |
| `contentHash` | `string` | Yes | MD5 hash for dedup |
| `fileSize` | `number` | Yes | File size in bytes |
| `account` | `string` | Yes | PE fund company (parsed from filename) |
| `fund` | `string` | Yes | PE fund name (parsed from filename) |
| `docType` | `string` | Yes | Document type (parsed from filename) |
| `docName` | `string` | Yes | Document name (parsed from filename) |
| `publishedDate` | `string` (ISO 8601) | Yes | Published date (parsed from filename) |
| `effectiveDate` | `string` (ISO 8601) | Yes | Effective date (parsed from filename) |

#### PDF file message example (→ `email-intake` queue)

```json
{
  "fileId": "sftp-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "originalFilename": "HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf",
  "fileType": "pdf",
  "blobPath": "/attachments/sftp-a1b2c3d4/HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf",
  "intakeSource": "sftp",
  "receivedAt": "2026-03-09T14:30:00Z",
  "sftpPath": "/inbox/HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf",
  "contentHash": "d41d8cd98f00b204e9800998ecf8427e",
  "fileSize": 245780,
  "account": "HorizonCapital",
  "fund": "GrowthFundIII",
  "docType": "CapitalCall",
  "docName": "Q4Notice",
  "publishedDate": "2026-03-09",
  "effectiveDate": "2026-04-01"
}
```

#### CSV file message example (→ SharePoint directly, no Service Bus)

CSV/Excel files are NOT sent to Service Bus. The Logic App uploads them directly to SharePoint.
The Cosmos DB record captures the SharePoint path after upload. No Service Bus message is produced.
```

**Compatibility note**: The existing email intake message format (`emailId`, `from`, `subject`, `bodyText`, `attachmentPaths`, etc.) remains unchanged. The agent detects SFTP-sourced messages by checking for the `intakeSource: "sftp"` field (or absence of `emailId`).

---

### 3. SFTP File Blob (Azure Blob Storage)

**Container**: `attachments`  
**Storage account**: `stdocprocdevizr2ch55`

#### Path convention

| Source | Pattern | Example |
|---|---|---|
| Email attachment | `/attachments/{emailId}/{filename}` | `/attachments/AAMkADI5.../invoice.pdf` |
| Link download | `/attachments/{emailId}/{filename}` | `/attachments/AAMkADI5.../report.pdf` |
| SFTP file | `/attachments/sftp-{fileId}/{filename}` | `/attachments/sftp-a1b2c3d4/HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf` |

**Notes**:
- The `sftp-` prefix in the blob path distinguishes SFTP-sourced files from email attachments.
- The `{fileId}` is a UUID generated by the Logic App when the file is detected.
- Each SFTP file gets its own subfolder (single file per folder, unlike emails which may have multiple attachments).

---

## Migration Plan

### Step 1: Infrastructure (Bicep)

Update `infrastructure/modules/cosmos-db.bicep`:
- Rename container resource from `'emails'` to `'intake-records'`
- Add composite index on `intakeSource` + `status` for efficient queries
- Keep all existing indexes (status+receivedAt, confidenceLevel+receivedAt)

### Step 2: Data Migration Script

Create a one-time Python migration script (`utils/migrate_container.py`):
1. Read all documents from `emails` container
2. Add `intakeSource: "email"` to each document
3. Upsert each document into `intake-records` container
4. Verify record count matches
5. Script MUST be idempotent (safe to re-run)

### Step 3: Code Reference Updates

See research.md § 4 for the full list of files with `emails` container references.

### Step 4: Validation

- Verify all documents exist in `intake-records` with correct `intakeSource` field
- Verify dashboard loads correctly from new container
- Verify agent processes email-intake queue messages using new container
- Verify SFTP CSV/Excel records contain parsed metadata (`account`, `fund`, `docType`, etc.) and `sharepointPath`
- Delete old `emails` container after validation period
