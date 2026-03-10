# Feature Specification: SFTP File Intake Channel

**Feature Branch**: `003-sftp-intake`  
**Created**: 2026-03-09  
**Status**: Draft  
**Input**: User description: "Add a second input channel triggered by new files arriving in a specific SFTP folder. Authentication via SSH private key. Files can be Excel, CSV, or PDF. Excel and CSV go directly to archival-pending queue with metadata logged in Cosmos DB. PDFs undergo full classification (text extraction via Document Intelligence) or triage-only processing depending on pipeline mode. The same dashboard is used for observability."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ingest Excel and CSV Files from SFTP (Priority: P1)

A business user or automated system deposits an Excel (.xlsx/.xls) or CSV (.csv) file into a monitored SFTP folder. The system detects the new file, authenticates to the SFTP server using an SSH private key, downloads the file, parses document metadata from the filename (Account, Fund, Doc type, Name, Published date, Effective date), stores the file in Azure Blob Storage as a backup, logs file metadata into Cosmos DB, and uploads the file directly to a SharePoint document library using a structured folder path: `{root}/{first letter of Account}/{Account}/{Fund}/{filename}`. Classification is bypassed entirely since these structured file types do not require content analysis.

**Why this priority**: This is the simplest end-to-end SFTP intake flow. It validates the entire SFTP connectivity and ingestion pipeline (SSH-key auth, file detection, download, metadata parsing, storage, SharePoint upload) without the complexity of classification. It delivers immediate value for structured data files.

**Independent Test**: Place a CSV file and an Excel file into the monitored SFTP folder. Verify both files are downloaded, stored in blob storage, their metadata appears in Cosmos DB, and both files are uploaded to the correct SharePoint folder paths.

**Acceptance Scenarios**:

1. **Given** a new `.csv` file with a valid filename convention (e.g., `HorizonCapital_GrowthFundIII_CapitalCall_Q4Report_20260309_20260301.csv`) appears in the monitored SFTP folder, **When** the intake flow processes it, **Then** the system downloads the file via SSH-key-authenticated SFTP, parses metadata from the filename (Account=`HorizonCapital`, Fund=`GrowthFundIII`, DocType=`CapitalCall`, etc.), stores the file in blob storage at `/attachments/sftp-{fileId}/{filename}`, creates a document record in Cosmos DB with file and parsed metadata, and uploads the file to SharePoint at `{root}/H/HorizonCapital/GrowthFundIII/{filename}`.
2. **Given** a new `.xlsx` file appears in the monitored SFTP folder, **When** the intake flow processes it, **Then** the system follows the same flow as CSV — download, parse metadata, store in blob, log in Cosmos DB, upload to SharePoint — without any content extraction or classification step.
3. **Given** a new `.xls` (legacy Excel) file appears in the monitored SFTP folder, **When** the intake flow processes it, **Then** the system treats it identically to `.xlsx` files.
4. **Given** a CSV or Excel file whose filename does not match the expected metadata convention (e.g., wrong number of segments), **When** the intake flow attempts to process it, **Then** the system logs a parsing error, creates a Cosmos DB record with `status: "error"`, and does NOT upload to SharePoint. The file remains on the SFTP server for manual investigation.

---

### User Story 2 - Ingest PDF Files with Full Classification (Priority: P1)

A PDF file is deposited into the monitored SFTP folder. The system downloads it via SSH-key-authenticated SFTP, stores it in blob storage, logs file metadata in Cosmos DB, and sends it to a processing queue for classification using the same classification pipeline as email attachments — but without any email-specific metadata (no sender, subject, or email body). Classification results determine routing: archival-pending (high confidence), human-review (low confidence), or discarded (not relevant).

**Why this priority**: PDF classification is the core differentiating capability for the SFTP channel. PDFs require intelligent classification, making this the most complex and valuable flow.

**Independent Test**: Place a PDF file into the monitored SFTP folder with the pipeline configured in full mode. Verify the file is downloaded, stored, classified, and routed to the appropriate output queue.

**Acceptance Scenarios**:

1. **Given** a new `.pdf` file appears in the monitored SFTP folder and the pipeline is in full mode, **When** the intake flow processes it, **Then** the system downloads the file via SSH-key-authenticated SFTP, stores it in blob storage at `/attachments/sftp-{fileId}/{filename}`, logs metadata in Cosmos DB, classifies the content, and routes the message to the `archival-pending` queue (confidence ≥ 65%) or `human-review` queue (confidence < 65%).
2. **Given** a PDF file that is not relevant to the domain, **When** classification runs, **Then** the file is routed to the `discarded` queue.

---

### User Story 3 - Ingest PDF Files in Triage-Only Mode (Priority: P2)

When the system is configured in triage-only mode (as defined in feature 002-pipeline-config), a PDF file deposited into the SFTP folder is downloaded via SSH-key-authenticated SFTP, stored in blob storage, logged in Cosmos DB, and forwarded to the `triage-complete` queue without undergoing classification.

**Why this priority**: This mode supports deployments where an external IDP system handles classification. It must respect the pipeline configuration established in feature 002.

**Independent Test**: Set `PIPELINE_MODE` to `triage-only`, place a PDF into the SFTP folder, and verify it is logged and routed to the `triage-complete` queue without classification.

**Acceptance Scenarios**:

1. **Given** the pipeline is in triage-only mode and a new `.pdf` file appears in the SFTP folder, **When** the intake flow processes it, **Then** the system downloads the file via SSH-key-authenticated SFTP, stores it in blob storage at `/attachments/sftp-{fileId}/{filename}`, logs metadata in Cosmos DB, and routes the message to the `triage-complete` queue. No classification step is executed.
2. **Given** triage-only mode and a PDF file, **When** processing completes, **Then** the Cosmos DB record shows `pipelineMode: "triage-only"` and steps executed do not include classification.

---

### User Story 4 - SFTP File Visibility on Dashboard (Priority: P3)

An admin or operator viewing the existing web dashboard can see SFTP-sourced files alongside email-sourced documents. The dashboard displays the intake source (SFTP vs. email), file type, processing status, and classification results (for PDFs). Operators can use this to monitor the SFTP channel health and investigate processing issues.

**Why this priority**: Operational visibility is important but secondary to the intake flows themselves functioning correctly. The dashboard already exists; this extends it with SFTP-sourced records.

**Independent Test**: Process files via SFTP, then view the dashboard. Verify SFTP-sourced documents appear with a source indicator distinguishing them from email-sourced documents.

**Acceptance Scenarios**:

1. **Given** files have been processed via the SFTP channel, **When** an operator views the dashboard, **Then** SFTP-sourced documents appear in the same list as email-sourced documents, with a visual indicator showing the intake source as "SFTP".
2. **Given** an SFTP-sourced PDF has been classified, **When** an operator views its details, **Then** the classification results and processing steps are visible.

---

### Edge Cases

- What happens when a file type other than Excel, CSV, or PDF (e.g., .docx, .txt, .zip) appears in the SFTP folder? The system MUST log a warning with the filename and unsupported type, skip the file, and not move or delete it from the SFTP folder.
- What happens when the SFTP server is unreachable or the SSH private key is rejected? The system MUST log an error with connection details and retry according to the Logic App's built-in retry policy. No files are lost since they remain on the SFTP server.
- What happens when a file is zero bytes or corrupted? The system MUST log the issue, create the Cosmos DB record with `status: "error"` and error details, and leave the file on the SFTP server for manual investigation. The file is NOT moved to `/processed/`.
- What happens when the same file appears in the SFTP folder again (duplicate detection)? The system MUST use the file path and a content hash to detect duplicates. If a duplicate is detected, the file is skipped and logged as a duplicate.
- What happens when multiple files arrive simultaneously in the SFTP folder? The system MUST process each file independently. A failure in one file MUST NOT block processing of other files.
- What happens to processed files on the SFTP server after successful ingestion? The system MUST move processed files to an archive subfolder on the SFTP server (e.g., `/processed/`) to prevent reprocessing.
- What happens when a filename does not match the expected metadata convention (wrong number of segments, missing fields)? The system MUST log a warning with the filename and parsing error, create a Cosmos DB record with `status: "error"` and error details, and skip SharePoint upload. The file is NOT moved to `/processed/` and remains on the SFTP server for manual investigation.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support a second intake channel triggered by new files appearing in a configured SFTP folder, in addition to the existing email-triggered intake.
- **FR-002**: SFTP server authentication MUST use SSH private key authentication. The private key MUST be stored in Azure Key Vault and retrieved at Bicep deployment time via `getSecret()`. The key MUST be injected into a `Microsoft.Web/connections` API Connection resource for the `sftpWithSsh` managed connector, following the same provisioning pattern as existing connections (azureblob, documentdb, servicebus).
- **FR-003**: The system MUST monitor a configurable SFTP folder path for new files and process them as they arrive.
- **FR-004**: The system MUST support three file types from the SFTP channel: Excel (.xlsx, .xls), CSV (.csv), and PDF (.pdf).
- **FR-005**: Excel and CSV files MUST be uploaded directly to a SharePoint document library after download, filename metadata parsing, blob storage (backup), and Cosmos DB logging — without any content extraction or classification step. The `archival-pending` Service Bus queue is NOT used for SFTP-sourced CSV/Excel files.
- **FR-006**: PDF files MUST follow the same download-store-log-queue pattern as Excel and CSV files: download via SSH-key-authenticated SFTP, store in blob storage at `/attachments/sftp-{fileId}/{filename}`, and log metadata in Cosmos DB.
- **FR-007**: In full-pipeline mode, PDF files MUST be sent to the processing queue for classification using the same classification logic as email attachments, but without email-specific metadata (no sender, subject, or email body). Classification results determine routing: archival-pending (high confidence), human-review (low confidence), or discarded (not relevant).
- **FR-008**: In triage-only mode, PDF files MUST have metadata logged and be routed to the configured `triage-complete` queue without classification.
- **FR-009**: All SFTP-sourced files MUST have metadata logged in the Cosmos DB `intake-records` container (renamed from `emails`) using a unified schema. Records are distinguished by the `intakeSource` field (`"email"` or `"sftp"`). Shared base fields (`id`, `intakeSource`, `status`, `receivedAt`, `processedAt`, `classification`, `pipelineMode`, `stepsExecuted`, `queue`, `blobPath`) MUST be present on all records regardless of source. Channel-specific fields coexist without conflict. Existing email records MUST be migrated to the new container, and all code references (Logic App workflow, agent tools, dashboard queries) MUST be updated.
- **FR-010**: SFTP file metadata records in Cosmos DB MUST include at minimum: unique file identifier, original filename, file type, file size, intake source ("sftp"), SFTP folder path, timestamps (file detected, processing started, processing completed), processing status, and blob storage path.
- **FR-011**: Downloaded SFTP files MUST be stored in Azure Blob Storage using the path convention `/attachments/sftp-{fileId}/{filename}`.
- **FR-012**: The system MUST handle unsupported file types by logging a warning and skipping the file without removing it from the SFTP folder.
- **FR-013**: The system MUST detect and skip duplicate files based on file path and content hash to prevent reprocessing.
- **FR-014**: Successfully processed files MUST be moved to an archive subfolder on the SFTP server (e.g., `/processed/`) to prevent reprocessing.
- **FR-015**: Processing failures MUST create a Cosmos DB record with `status: "error"` and error details, and leave the affected file on the SFTP server for manual investigation. The file is NOT moved to `/processed/`. Failures in one file MUST NOT block processing of other files.
- **FR-016**: The dashboard MUST display SFTP-sourced documents alongside email-sourced documents with a clear source indicator.
- **FR-017**: The system MUST parse document metadata from the SFTP filename using a configurable delimiter convention (default: underscore `_`). Required metadata fields extracted from the filename: Account (PE fund company), Fund (PE fund name), Doc type, Name, Published date (YYYYMMDD), Effective date (YYYYMMDD). Expected format: `{Account}_{Fund}_{DocType}_{Name}_{PublishedDate}_{EffectiveDate}.{ext}`. If parsing fails (wrong number of segments or invalid date format), the file MUST be logged with an error status and skipped without being uploaded to SharePoint.
- **FR-018**: Excel and CSV files from SFTP MUST be uploaded to SharePoint Online using the Logic App SharePoint connector ("Create file" action). The target folder path MUST follow the convention: `{configurable root}/{first letter of Account}/{Account}/{Fund}/{filename}`. Folders MUST be created automatically if they do not exist. The SharePoint site URL and root document library path MUST be configurable via Logic App parameters.
- **FR-019**: The Cosmos DB record for SFTP-sourced CSV/Excel files MUST include the parsed metadata fields (`account`, `fund`, `docType`, `publishedDate`, `effectiveDate`) and the resulting SharePoint path (`sharepointPath`) after successful upload.

### Constitution Alignment Requirements *(mandatory)*

- **CAR-001 (Code Simplicity)**: The SFTP intake MUST be implemented as a separate Logic App workflow that feeds into the existing processing pipeline via Service Bus queues. Avoid duplicating classification logic — reuse the existing agent processing path.
- **CAR-002 (UX Simplicity)**: Dashboard changes MUST be minimal — add a source column/indicator to distinguish SFTP from email intake. No new dashboards or complex filtering.
- **CAR-003 (Responsive Design)**: Any dashboard changes MUST remain usable on common desktop and mobile viewport sizes.
- **CAR-004 (Dependencies)**: The SFTP-SSH and SharePoint connectors are both built-in Logic App managed connectors — no new code dependencies required. SharePoint Online is a new service dependency for archival of CSV/Excel files. The SharePoint connector requires an Entra ID app registration with `Sites.ReadWrite.All` application permission and a client secret stored in Key Vault.
- **CAR-005 (Auth)**: SFTP authentication MUST use SSH private key auth with the key stored in Azure Key Vault and injected into the API Connection resource at Bicep deployment time. Azure service access (Storage, Cosmos DB, Service Bus) MUST continue using Entra ID managed identity.
- **CAR-006 (Azure/Microsoft SDKs)**: The SFTP intake Logic App MUST use the Azure Logic App SFTP-SSH managed connector. Blob storage, Cosmos DB, and Service Bus interactions MUST use official Azure connectors/SDKs.
- **CAR-007 (Testing Scope)**: Tests MUST focus on: (1) CSV/Excel files are uploaded to the correct SharePoint folder, (2) PDF files in full mode reach classification queue, (3) PDF files in triage-only mode reach triage-complete, (4) unsupported file types are skipped. No exhaustive SFTP connectivity tests.
- **CAR-008 (Logging Discipline)**: All SFTP file processing events (detection, download, storage, routing) MUST be logged via structured logging. No print statements.

### Key Entities

- **Intake Record** (Cosmos DB `intake-records` container — unified schema, renamed from `emails`): Both email and SFTP records share the same container, distinguished by `intakeSource` (`"email"` or `"sftp"`). Shared base fields present on all records: `id`, `intakeSource`, `status` (partition key: `"received"`, `"processing"`, `"classified"`, `"archived"`, `"discarded"`, `"needs_review"`, `"error"`), `receivedAt`, `processedAt`, `classification`, `pipelineMode`, `stepsExecuted`, `queue`, `blobPath`. Email-specific fields (present only when `intakeSource: "email"`): `emailId`, `from`, `subject`, `emailBody`, `hasAttachments`, `attachmentsCount`, `attachmentPaths`. SFTP-specific fields (present only when `intakeSource: "sftp"`): `originalFilename`, `fileType` (`"csv"`, `"xlsx"`, `"xls"`, `"pdf"`), `fileSize`, `sftpPath`, `contentHash`, `account` (PE fund company, parsed from filename), `fund` (PE fund name, parsed from filename), `docType` (document type, parsed from filename), `publishedDate` (ISO 8601, parsed from filename), `effectiveDate` (ISO 8601, parsed from filename), `docName` (document name, parsed from filename), `metadataParseError` (error details if filename parsing fails, null on success), `sharepointPath` (SharePoint destination path, populated for CSV/Excel files after upload).
- **SFTP Intake Message** (Service Bus — PDF files only): The message sent by the SFTP Logic App to the `email-intake` queue for PDF files requiring classification. Contains: `fileId`, `originalFilename`, `fileType`, `blobPath`, `intakeSource` (`"sftp"`), `receivedAt`, plus parsed metadata (`account`, `fund`, `docType`, `docName`, `publishedDate`, `effectiveDate`). CSV/Excel files do NOT use Service Bus — they are uploaded directly to SharePoint by the Logic App.
- **SFTP Connection Configuration**: Logic App connection parameters including SFTP host, port, username, monitored folder path, SSH private key (retrieved from Key Vault via Bicep `getSecret()`), archive folder path, filename metadata delimiter, SharePoint site URL, SharePoint root document library path, and SharePoint Entra ID app registration credentials (client ID and client secret from Key Vault). The SFTP-SSH and SharePoint API Connections are provisioned as `Microsoft.Web/connections` resources in Bicep alongside existing connections. Configured via Logic App parameters and Bicep parameter files. **Required new parameters**: Key Vault secrets (pre-provisioned): `sftp-private-key`, `sharepoint-client-secret`. Bicep parameters (added to `dev.bicepparam` / `prod.bicepparam`): `sftpHost`, `sftpPort`, `sftpUsername`, `sftpFolderPath`, `sftpArchiveFolderPath`, `keyVaultName`, `sharepointClientId`, `sharepointTenantId`, `sharepointSiteUrl`, `sharepointDocLibraryPath`. Logic App parameters: `filenameDelimiter` (default `_`).

## Clarifications

### Session 2026-03-09

- Directive: Remove Document Intelligence text extraction from PDFs in initial implementation → Applied to US2, US3, FR-006, FR-007, FR-008; PDFs follow the same download-store-log-queue pattern as Excel/CSV.
- Directive: Explicit blob storage path convention `/attachments/sftp-{fileId}/{filename}` in US2 and US3 acceptance criteria → Applied to match US1 pattern.
- Q: How should SFTP file records coexist with email records in Cosmos DB? → A: Unified schema in same `emails` container — shared base fields (`id`, `intakeSource`, `status`, `receivedAt`, `processedAt`, `classification`, `pipelineMode`, `stepsExecuted`, `queue`, `blobPath`) + `intakeSource` discriminator (`"email"` or `"sftp"`), with channel-specific fields coexisting.- Q: Should the Cosmos DB container be renamed from `emails` to a source-agnostic name? → A: Rename to `intake-records`. Existing data must be migrated and all code references (Logic App, agent, dashboard) updated.
- Directive: CSV/Excel files from SFTP must be archived directly to a SharePoint document library using folder path `{root}/{first letter of Account}/{Account}/{Fund}/{filename}` instead of the `archival-pending` Service Bus queue. The Logic App handles SharePoint upload directly (no intermediate queue). → Applied to US1, FR-005, FR-017, FR-018, FR-019.
- Clarification: Two document types in the SFTP pipeline: (1) Pre-processed documents (Excel/CSV) — metadata is analyzed/logged, then stored directly in SharePoint; (2) Documents to be processed (PDFs) — sent to the processing queue for classification in full mode, or to `triage-complete` queue for an external Intelligence Document Processing (IDP) app in triage-only mode. → Confirmed alignment with US1 (CSV/Excel→SharePoint), US2 (PDF full classification), US3 (PDF triage-only→`triage-complete` for IDP). Fixed stale references to `archival-pending` in US1 Independent Test and CAR-007. Aligned FR-015 and EC-3 error handling to use Cosmos DB `status: "error"` + file stays on SFTP (no dead-letter queue). Removed stale "extracted text summary" from US4. Added `docName` and `metadataParseError` to Key Entities.
- Q: How should the SFTP-SSH API Connection be provisioned and authenticated? → A: SSH private key stored in Azure Key Vault, retrieved at Bicep deployment time via `getSecret()`, and injected into a `Microsoft.Web/connections` API Connection resource for the `sftpWithSsh` managed connector. This follows the same pattern as existing API Connections (azureblob, documentdb, servicebus) but uses key-based auth instead of managed identity.
- Q: How should the SharePoint API Connection be authenticated? → A: Entra ID app registration with application permissions (`Sites.ReadWrite.All`). Client secret stored in Azure Key Vault, retrieved at Bicep deployment time via `getSecret()`, and injected into a `Microsoft.Web/connections` API Connection resource for the `sharepointonline` managed connector. This enables non-interactive (background) file uploads without user-delegated tokens.
- Q: Have environment variables or Bicep parameters been created for the SFTP and SharePoint connections? → A: No — not yet created. The spec now enumerates the full parameter inventory: **Key Vault secrets** (pre-provisioned by infra/security team): `sftp-private-key`, `sharepoint-client-secret`. **Bicep parameters** (new, added to parameter files): `sftpHost`, `sftpPort`, `sftpUsername`, `sftpFolderPath`, `sftpArchiveFolderPath`, `keyVaultName`, `sharepointClientId`, `sharepointTenantId`, `sharepointSiteUrl`, `sharepointDocLibraryPath`. **Logic App parameters**: `filenameDelimiter` (default `_`). These must be created during infrastructure implementation tasks.
- Q: How does the system determine PE fund company and fund name for the SharePoint folder path? → A: Option B — filename convention. Each SFTP document filename encodes metadata fields: Account (PE fund company), Fund (PE fund name), Doc type, Name, Published date, Effective date. Format: `{Account}_{Fund}_{DocType}_{Name}_{YYYYMMDD}_{YYYYMMDD}.{ext}`. The SFTP source does not use folder/subfolder structure within the fund manager room.
## Assumptions

- The SFTP server is a standard SSH/SFTP server that supports SSH private key authentication. The server is externally managed and always available during business hours.
- The Logic App SFTP-SSH managed connector supports SSH private key authentication and file-change triggers for the monitored folder.
- The SSH private key for SFTP authentication is provisioned and stored in Azure Key Vault by the infrastructure/security team before this feature is deployed. The Bicep deployment retrieves it via `getSecret()` and provisions the `sftpWithSsh` API Connection resource.
- The SharePoint Entra ID app registration and its client secret are created by the infrastructure/security team before deployment. The client secret is stored in Azure Key Vault. Both the SFTP private key and SharePoint client secret Key Vault secrets must exist before running the Bicep deployment.
- The monitored SFTP folder path and archive subfolder path are configurable via Logic App parameters.
- Excel and CSV files are considered fully structured and do not require content analysis or classification — they are uploaded directly to SharePoint by the Logic App (not routed via `archival-pending` Service Bus queue).
- SFTP filenames follow a consistent underscore-delimited convention: `{Account}_{Fund}_{DocType}_{Name}_{PublishedDate}_{EffectiveDate}.{ext}`. The exact delimiter and field ordering are configurable via Logic App parameters. If real filenames use a different convention, the Logic App parsing expressions must be updated.
- The SharePoint site and document library are pre-provisioned. An Entra ID app registration with `Sites.ReadWrite.All` application permission is created by the infrastructure/security team before deployment. The app's client secret is stored in Azure Key Vault. The Bicep deployment retrieves it via `getSecret()` and provisions the `sharepointonline` API Connection resource.
- PDF classification reuses the same agent-based classification logic as email attachments, with the input adapted to exclude email-specific metadata fields.
- The Cosmos DB container is renamed from `emails` to `intake-records` to reflect its multi-source nature. Existing email data must be migrated to the new container. All code references (`CONTAINER_EMAILS` in agent tools, Logic App Cosmos DB connector path, dashboard queries) must be updated. The `/status` partition key strategy works for both document types since SFTP records use the same status lifecycle. Existing email records MUST be backfilled with `intakeSource: "email"` during migration.
- The existing dashboard queries Cosmos DB and can be extended with minimal changes to display records with `intakeSource: "sftp"`.
- File size limits for SFTP files follow the same constraints as email attachments (default 50 MB max).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Excel and CSV files deposited in the SFTP folder are downloaded, metadata-parsed, stored in blob storage, logged in Cosmos DB, and uploaded to the correct SharePoint folder within 2 minutes of file detection.
- **SC-002**: PDF files deposited in the SFTP folder are fully processed (download, storage, classification, routing) within 5 minutes of file detection in full-pipeline mode.
- **SC-003**: 100% of SFTP-sourced files appear on the existing dashboard with correct source identification within 1 minute of processing completion.
- **SC-004**: Unsupported file types are skipped without affecting processing of valid files in the same batch.
- **SC-005**: No existing email intake functionality is broken by the introduction of the SFTP channel.
- **SC-006**: SFTP connection failures are logged and do not result in data loss — unprocessed files remain on the SFTP server for retry.