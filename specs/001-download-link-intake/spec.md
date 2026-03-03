# Feature Specification: Download-Link Intake

**Feature Branch**: `001-download-link-intake`  
**Created**: 2026-02-26  
**Status**: Draft  
**Input**: User description: "Enrich the email intake to handle emails that contain a download link in the body instead of traditional attachments. The intake flow should detect the link, download the document, and store it in the storage account using the same convention as for regular attachments."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Detect and Download Linked Document (Priority: P1)

An email arrives at the monitored inbox containing no non-inline attachments. Instead, the email body includes a URL that points to a downloadable document (e.g., a PDF hosted on a third-party portal or cloud storage). The intake flow detects the download link in the email body, downloads the document, stores it in the Azure Storage Account under the same path convention used for regular attachments (`/attachments/{emailId}/{filename}`), updates the Cosmos DB email record with the resulting attachment path, and sends the message to the Service Bus intake queue in the same format as any other email with attachments.

**Why this priority**: This is the core value of the feature. Without it, emails containing only download links are processed as if they had no attachments, meaning the linked document is never captured and downstream classification operates on the email body alone.

**Independent Test**: Send an email with no traditional attachments but with a body containing a single valid download link pointing to a PDF. Verify the document is downloaded, stored in the storage account under the expected path, the Cosmos DB record reflects the attachment, and the Service Bus message includes the attachment path.

**Acceptance Scenarios**:

1. **Given** a new email arrives with no non-inline attachments and the body contains a valid HTTP/HTTPS download link to a document, **When** the intake flow processes the email, **Then** the system downloads the document from the link, stores it in the storage account at `/attachments/{emailId}/{derived-filename}`, records the attachment path in the Cosmos DB email document, sets `hasAttachments` to true and `attachmentsCount` to 1, and sends the enriched message to the `email-intake` queue.
2. **Given** a new email arrives with both non-inline attachments and a download link in the body, **When** the intake flow processes the email, **Then** the system stores the traditional attachments as before AND also downloads the linked document, storing it under the same path convention, so the Cosmos DB record and Service Bus message include all attachment paths.

---

### User Story 2 - Graceful Handling of Unavailable or Invalid Links (Priority: P2)

An email body contains a URL that looks like a download link, but the target is unreachable (timeout, 404, DNS failure) or returns content that is not a supported document type. The intake flow should handle this gracefully: the email MUST still be ingested and sent to the intake queue, but without the downloaded attachment. The failure should be logged and visible for operational monitoring.

**Why this priority**: Download links may be expired, password-protected, or pointing to non-document resources. The system must not break the overall processing pipeline because of a single failed download.

**Independent Test**: Send an email with a broken or expired download link. Verify the email is still ingested into Cosmos DB and forwarded to the Service Bus queue, with appropriate indicators that the download failed.

**Acceptance Scenarios**:

1. **Given** a new email arrives with a download link that returns a non-success HTTP status (e.g., 404, 500), **When** the intake flow attempts to download the document, **Then** the system logs the failure, continues processing the email without the linked attachment, sets `hasAttachments` based on any other non-inline attachments present, and sends the email to the intake queue.
2. **Given** a new email arrives with a download link that times out after a reasonable wait, **When** the intake flow attempts to download the document, **Then** the system treats the download as failed, logs the timeout, and processes the email normally without the linked document.
3. **Given** a new email arrives with a URL that resolves but returns non-document content (e.g., an HTML page), **When** the intake flow processes the response, **Then** the system discards the response, logs the unexpected content type, and continues processing the email.

---

### User Story 3 - Dashboard Visibility for Link-Sourced Attachments (Priority: P3)

An admin or business user viewing the web dashboard can see whether an email's attachment was sourced from a download link versus a traditional attachment. This helps operators understand where documents originated and troubleshoot intake issues.

**Why this priority**: Operational visibility is important but secondary to the core download and error-handling functionality. The dashboard already shows attachment details; this extends it with source-origin metadata.

**Independent Test**: Process an email with a download link, then open the dashboard and verify the email entry shows the attachment with an indicator that it was downloaded from a link.

**Acceptance Scenarios**:

1. **Given** an email has been processed with a link-sourced attachment, **When** an admin views the email details on the dashboard, **Then** the attachment entry shows a visual indicator (e.g., icon or label) that it was sourced from a download link, derived from the `source` field in the `attachmentPaths` object.

---

### Edge Cases

- What happens when an email body contains multiple download links? The system should attempt to download all of them and store each as a separate attachment under the email's path.
- What happens when the download link requires authentication (e.g., login-protected portal)? The system should treat this as a download failure (non-success HTTP response or redirect to a login page) and process the email without the linked attachment, logging the issue.
- What happens when the downloaded file is very large (e.g., exceeds the configured limit of 50 MB)? The system should enforce the size limit per download and skip files that exceed it, logging the skip reason.
- What happens when the email body contains URLs that are not download links (e.g., website links, social media)? The system should only attempt downloads for URLs that appear to reference downloadable documents (based on file extension, content-type hints, or a configurable pattern).
- What happens when the same email has both regular attachments and one or more download links? All should be stored and counted together in the Cosmos DB record and Service Bus message.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The intake flow MUST scan the email body for URLs that reference downloadable documents when the email has no non-inline attachments (or in addition to existing attachments).
- **FR-002**: The intake flow MUST download the document from a detected link using standard HTTPS.
- **FR-003**: Downloaded documents MUST be stored in Azure Blob Storage using the same path convention as regular attachments: `/attachments/{emailId}/{filename}`.
- **FR-004**: The filename for a link-sourced document MUST be derived from the URL path, the Content-Disposition header, or a generated name if neither source provides a usable filename.
- **FR-005**: The Cosmos DB email record MUST store attachments in `attachmentPaths` as an array of objects, each containing at minimum `path` (string, blob path) and `source` (`"attachment"` for traditional, `"link"` for link-sourced). The `source` field is the canonical mechanism for distinguishing attachment origins. `hasAttachments` and `attachmentsCount` MUST reflect all attachments (regular + link-sourced).
- **FR-006**: The Service Bus message MUST include attachment entries in the same object format as the Cosmos DB record (`{"path": "...", "source": "..."}`) so downstream consumers can distinguish attachment origins. Existing consumers that only read paths will need a minor update to access the `path` field.
- **FR-007**: Download failures (network errors, non-success HTTP status, timeout, unsupported content type) MUST NOT block email processing. The email MUST still be ingested and forwarded.
- **FR-008**: Download failures MUST be logged with enough context to diagnose (URL attempted, HTTP status or error type, email ID).
- **FR-009**: The system MUST enforce a maximum file-size limit for downloads to prevent unbounded resource consumption. Files exceeding the limit MUST be skipped and the skip logged.
- **FR-010**: The system MUST apply a reasonable timeout for each download attempt.
- **FR-011**: The web dashboard SHOULD display the attachment source (link vs. traditional) when showing email details.

### Constitution Alignment Requirements *(mandatory)*

- **CAR-001 (Code Simplicity)**: The download-link detection and fetching logic MUST be implemented in a focused, single-responsibility module. Avoid over-engineering link-detection patterns; start with URL patterns matching common document file extensions. 
- **CAR-002 (UX Simplicity)**: Dashboard changes MUST be minimal — a small visual indicator on attachment entries is sufficient. No new screens or complex filtering controls.
- **CAR-003 (Responsive Design)**: Any dashboard changes MUST remain legible and functional on mobile viewports.
- **CAR-004 (Dependencies)**: Link downloading SHOULD use existing project HTTP capabilities or Python standard library. No new HTTP client library unless the existing stack cannot handle streaming downloads.
- **CAR-005 (Auth)**: Storage account and Cosmos DB access MUST use Microsoft Entra ID (managed identity) as already established. The HTTP download of the external link uses standard unauthenticated HTTPS (external resource).
- **CAR-006 (Azure/Microsoft SDKs)**: Blob upload MUST use the official Azure Storage Python SDK. Cosmos DB updates MUST use the official Azure Cosmos DB Python SDK.
- **CAR-007 (Testing Scope)**: Focused tests covering: (1) link detection in email body, (2) successful download and storage, (3) graceful failure handling. No exhaustive URL-parsing fuzzing.
- **CAR-008 (Logging Discipline)**: All download attempts, successes, and failures MUST be logged via structured logging with severity levels. No print statements.

### Key Entities

- **Email Document** (Cosmos DB `emails` container): Existing entity representing an ingested email. `attachmentPaths` changes from a flat string array to an array of objects: `[{"path": "<emailId>/<filename>", "source": "attachment"|"link"}]`. Key attributes: `id`, `hasAttachments`, `attachmentsCount`, `attachmentPaths` (array of objects), `status`.
- **Attachment Blob** (Azure Storage `/attachments/{emailId}/{filename}`): The stored document file. No schema change — the blob itself is unchanged; only the origin of the blob differs (uploaded from email attachment bytes vs. downloaded from an external link).
- **Service Bus Intake Message**: Existing message schema sent to `email-intake` queue. `attachmentPaths` changes from a flat string array to the same array-of-objects format as the Cosmos DB record. Downstream consumers require a minor update to read the `path` field from each object.

## Clarifications

### Session 2026-02-26

- Q: How should the attachment source be represented in the Cosmos DB email document? → A: Change `attachmentPaths` to an array of objects: `[{"path": "...", "source": "link"|"attachment"}]`.

## Assumptions

- The download links point to publicly accessible (unauthenticated) HTTPS endpoints. Authentication-protected links are out of scope for this iteration and treated as download failures.
- "Document" types of interest are: PDF, DOCX/DOC, XLSX/XLS, CSV, PPTX/PPT, TXT, and ZIP. URLs are filtered by extension pattern `\.(pdf|docx?|xlsx?|csv|pptx?|txt|zip)` — other content types are skipped. *(Resolved in research.md §1.)*
- ~~The existing Logic App flow may be extended to handle link detection and download, OR a Python-based pre-processing step may handle it.~~ **Resolved**: Python-based pre-processing step runs after Service Bus message receipt and before classification. *(Decided in plan.md.)*
- The file-size limit for downloads will default to a reasonable value (e.g., 50 MB) and be configurable.
- Download timeout will default to 30 seconds per file.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Emails containing download links instead of attachments are processed end-to-end — the linked document is stored and the email reaches the classification pipeline — within the same time expectations as emails with traditional attachments (difference < 30 seconds additional for download).
- **SC-002**: 100% of emails with download links that fail to download are still ingested into Cosmos DB and forwarded to the intake queue without manual intervention.
- **SC-003**: Operators can identify link-sourced attachments on the dashboard within 5 seconds of viewing an email's details.
- **SC-004**: No existing email intake functionality (regular attachments, emails without attachments) is broken by the introduction of this feature.
