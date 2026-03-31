# Feature Specification: Attachment Delivery Tracking for Email and Download Links

**Feature Branch**: `006-attachment-delivery-tracking`  
**Created**: 2026-03-30  
**Status**: Draft  
**Input**: User description: "Extend the version and delivery badge tracking — currently implemented only for SFTP intake records — to also cover email attachments and direct download links. Today, when a document arrives via SFTP, the system tracks version, deliveryCount, deliveryHistory, lastDeliveredAt, and contentHash on the Cosmos DB record, and the dashboard displays a version badge (v1, v2) and delivery count (2x, 3x) for SFTP records. For email and download-link sourced records, the dashboard just shows '—'. The goal is to implement equivalent tracking for email attachments and download link documents so operators can see when the same content has been received multiple times across any intake channel."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Track Delivery of Email Attachments (Priority: P1)

An email arrives at the monitored inbox with one or more PDF attachments. After the attachments are uploaded to blob storage, the system computes a content hash (MD5) for each attachment from the blob upload response. The Cosmos DB record for the email is enriched with `contentHash`, `version: 1`, `deliveryCount: 1`, `deliveryHistory`, and `lastDeliveredAt`. If a subsequent email from the same sender domain (same partition) arrives with an attachment that has an identical content hash, the system recognizes the duplicate content and increments `deliveryCount` on the original record rather than treating it as entirely new content.

**Why this priority**: Email is the primary intake channel. Without delivery tracking on email attachments, operators cannot determine if the same document has been sent repeatedly across different emails — a common occurrence with PE fund managers who resend capital call notices or NAV reports.

**Independent Test**: Send two separate emails from the same sender domain, each containing the same PDF file. Verify that the first email creates a record with `version: 1`, `deliveryCount: 1`. After the second email is processed, verify that `deliveryCount` is incremented to 2 on the matching record and the delivery history includes both deliveries.

**Acceptance Scenarios**:

1. **Given** an email arrives with a PDF attachment, **When** the Logic App uploads the attachment to blob storage, **Then** the Cosmos DB record includes `contentHash` (MD5 from the blob upload response), `version: 1`, `deliveryCount: 1`, `deliveryHistory` with one entry (`action: "new"`), and `lastDeliveredAt` set to the current timestamp.
2. **Given** a second email arrives from the same sender domain with an attachment whose content hash matches an existing record in the same partition, **When** the intake flow processes it, **Then** the system increments `deliveryCount` on the existing record, appends to `deliveryHistory` with `action: "duplicate"`, and updates `lastDeliveredAt` — without creating a duplicate record.
3. **Given** a second email arrives from the same sender domain with an attachment whose content hash differs from any existing record, **When** the intake flow processes it, **Then** the system creates a new intake record with `version: 1`, `deliveryCount: 1` (this is new content, not a re-delivery).

---

### User Story 2 - Track Delivery of Download-Link Documents (Priority: P1)

An email arrives containing a download link in the body. The Python link download tool fetches the document and uploads it to blob storage. After upload, the system extracts the content hash (MD5 from the blob upload response) and populates the same delivery tracking fields (`contentHash`, `version`, `deliveryCount`, `deliveryHistory`, `lastDeliveredAt`) on the Cosmos DB record. If a subsequent email contains a link to a document with matching content, the delivery is tracked as a duplicate.

**Why this priority**: Download links are the second most common delivery mechanism after direct attachments. Fund managers frequently send the same portal links to the same documents, and operators need to know when a link-sourced document is duplicate content.

**Independent Test**: Send two emails from the same sender domain, each with a download link pointing to the same document content. Verify the first creates a record with `deliveryCount: 1` and the second increments `deliveryCount` to 2.

**Acceptance Scenarios**:

1. **Given** an email arrives with a download link and the Python tool successfully downloads the file, **When** the file is uploaded to blob storage, **Then** the Cosmos DB record is updated with `contentHash`, `version: 1`, `deliveryCount: 1`, `deliveryHistory` (one entry with `action: "new"`), and `lastDeliveredAt`.
2. **Given** a second email from the same sender domain has a download link resolving to a file with a matching content hash, **When** the download tool processes it, **Then** `deliveryCount` is incremented on the matching record, `deliveryHistory` is appended with `action: "duplicate"`, and `lastDeliveredAt` is updated.
3. **Given** a download link fails (timeout, HTTP error, unsupported content type), **When** the failure is recorded, **Then** no delivery tracking fields are populated for that failed download attempt and the record continues to process normally.

---

### User Story 3 - Dashboard Shows Delivery Badges for All Intake Sources (Priority: P2)

An operator views the dashboard and sees version badges (v1, v2) and delivery count indicators (2x, 3x) for records from any intake channel — email, download link, or SFTP — wherever `version` and `deliveryCount` fields are populated. The current SFTP-only condition is removed so the existing UI logic applies universally.

**Why this priority**: The badge rendering already works for SFTP. This story simply removes the source filter so the existing UI logic applies universally. Low effort, high visibility improvement.

**Independent Test**: Process an email with a tracked attachment (having `version` and `deliveryCount`). View the dashboard and verify the version badge and delivery count appear for that email record, just as they do for SFTP records today.

**Acceptance Scenarios**:

1. **Given** an email record has `version: 1` and `deliveryCount: 3`, **When** an operator views the dashboard, **Then** the version badge shows "v1" and the delivery count shows "3x".
2. **Given** an email record has `version: 1` and `deliveryCount: 1`, **When** an operator views the dashboard, **Then** the version badge shows "v1" and no delivery multiplier is shown (same as SFTP behavior for single delivery).
3. **Given** a record has no `version` or `deliveryCount` fields (e.g., a legacy email record not yet enriched), **When** an operator views the dashboard, **Then** the column shows "—" as a fallback.

---

### User Story 4 - Content Update Detection for Email Attachments (Priority: P3)

When the same sender sends an attachment with the same filename but different content (different hash), the system increments both `version` and `deliveryCount` on the matching record and records the action as `"update"` in the delivery history. This mirrors the content-update behavior already implemented for SFTP files.

**Why this priority**: Content updates are less frequent for email than for SFTP, but still occur when a fund manager sends a corrected version of a document. This is a natural extension of the duplicate detection in US1.

**Independent Test**: Send two emails from the same sender domain, each with the same filename but different file content. Verify the second email triggers a version increment (v1 → v2) along with `deliveryCount` increment, and the history entry has `action: "update"`.

**Acceptance Scenarios**:

1. **Given** a record exists with `contentHash: "abc123"` and `version: 1`, **When** a new email from the same sender domain has an attachment with the same filename but `contentHash: "def456"`, **Then** the system updates the record to `version: 2`, increments `deliveryCount`, appends a `deliveryHistory` entry with `action: "update"` and the new content hash, and updates `lastDeliveredAt`.

---

### Edge Cases

- What happens when an email has multiple attachments with different content hashes? Each attachment is evaluated independently for dedup. The email record's primary `contentHash` reflects the first attachment. Additional attachments are tracked via their individual blob MD5 hashes in the `attachmentPaths` entries.
- What happens when the same document is received via both email and SFTP? Dedup is scoped per intake channel and partition. A document delivered via email and also via SFTP appears as separate records with independent delivery counts, since the dedup key and partition differ by design.
- What happens with legacy email records that lack delivery tracking fields? They continue to display "—" on the dashboard. Backfilling is not required; new fields are populated only on records processed after this feature is deployed.
- What happens when the blob upload response does not include Content-MD5? The system logs a warning and sets `contentHash` to null. The record is still created but cannot participate in content-based dedup.
- What happens when two different emails have attachments with the same filename but different senders (different partitions)? They are treated independently. Dedup is partition-scoped — different sender domains result in different partitions and no cross-partition matching.
- What happens when two emails with the same attachment content hash arrive near-simultaneously (race condition)? Accepted risk — the processing pipeline takes several seconds per email (Logic App trigger → blob upload → Cosmos query → write), making true simultaneous collision extremely unlikely. If a rare duplicate slips through, it is caught and consolidated on the next delivery. No concurrency control mechanism is added.
- What happens when a previously seen content hash is re-delivered after a version update? (e.g., v1=hashA, v2=hashB, then hashA arrives again.) The system compares only against the record's current `contentHash`. Since hashA ≠ hashB (current), it is treated as a content update: version increments to v3, `contentHash` reverts to hashA, and `deliveryHistory` records `action: "update"`. The system does not maintain a history of prior hashes for matching — only the current hash is compared.

## Clarifications

### Session 2026-03-30

- Q: How should the system handle concurrent dedup race conditions (two emails with same hash processed simultaneously)? → A: Accept — race window is negligible; next-delivery dedup catches rare duplicates.
- Q: When a previously seen content hash (hash A) is re-delivered after a version update (current hash is B), how should the system handle it? → A: Treat as content update (v3) — the current hash differs, so increment version and update contentHash back to A.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The email intake Logic App MUST extract the `Content-MD5` value via HTTP HEAD request to the uploaded blob for each attachment and store it as `contentHash` on the Cosmos DB intake record.
- **FR-002**: The email intake Logic App MUST populate `version: 1`, `deliveryCount: 1`, `deliveryHistory` (with one initial entry containing `action: "new"`), and `lastDeliveredAt` on every newly created email intake record.
- **FR-003**: The email intake Logic App MUST perform content-hash-based dedup within the same partition (sender domain + year-month). When a new attachment's content hash matches an existing record's `contentHash` in the same partition, the system MUST increment `deliveryCount`, append to `deliveryHistory` with `action: "duplicate"`, and update `lastDeliveredAt` — without creating a duplicate record.
- **FR-004**: When a new attachment's content hash does NOT match the current `contentHash` of an existing record but the filename matches an existing record within the same partition, the system MUST treat it as a content update: increment `version` and `deliveryCount`, append to `deliveryHistory` with `action: "update"`, update `contentHash` to the new value, and update `lastDeliveredAt`. This applies even if the new hash matches a prior version's hash — dedup compares only against the record's current `contentHash`, not historical values.
- **FR-005**: The Python link download tool MUST compute a content hash (MD5 via `hashlib.md5()` from the in-memory downloaded bytes) after downloading a link-sourced document and MUST update the Cosmos DB record with `contentHash`, `version`, `deliveryCount`, `deliveryHistory`, and `lastDeliveredAt` using the same logic as email attachments.
- **FR-006**: The Python link download tool MUST perform the same content-hash-based dedup as the email Logic App: detect duplicates (same hash) and content updates (same filename, different hash) within the same partition.
- **FR-007**: The dashboard MUST display version badges and delivery count indicators for ALL intake records that have `version` and `deliveryCount` fields populated, regardless of `intakeSource` value. The existing `intakeSource == 'sftp'` condition MUST be removed from the version/delivery badge rendering logic.
- **FR-008**: The dashboard MUST fall back to displaying "—" for records where `version` or `deliveryCount` fields are absent or null.
- **FR-009**: The `deliveryHistory` array entries MUST include `deliveredAt` (ISO 8601 timestamp), `contentHash` (MD5 string), and `action` (`"new"`, `"duplicate"`, or `"update"`).
- **FR-010**: Content-hash-based dedup for email attachments MUST be scoped to the same Cosmos DB partition (sender domain + year-month). Cross-partition or cross-month comparisons are NOT required.
- **FR-011**: When a download link fails (timeout, HTTP error, unsupported content type), the system MUST NOT populate delivery tracking fields for that failed download attempt. The record continues processing without delivery tracking for that asset.
- **FR-012**: For emails with multiple attachments, the email record's primary `contentHash` MUST be set from the first attachment. Each individual attachment's blob MD5 MUST be captured in the corresponding `attachmentPaths` entry to support per-attachment dedup.

### Constitution Alignment Requirements *(mandatory)*

- **CAR-001 (Code Simplicity)**: Delivery tracking logic for email/link MUST reuse the same field schema and 3-way routing pattern (new / duplicate / update) already implemented for SFTP. No separate tracking mechanism.
- **CAR-002 (UX Simplicity)**: Dashboard changes MUST be limited to removing the `intakeSource == 'sftp'` guard on the version/delivery badge column. No new UI components or dashboards.
- **CAR-003 (Responsive Design)**: The version/delivery badge column already exists and is responsive. No layout changes required.
- **CAR-004 (Dependencies)**: No new dependencies required. The blob upload `Content-MD5` response is already available from the Azure Blob connector. The Python `azure-storage-blob` SDK already returns content hash on upload.
- **CAR-005 (Auth)**: No authentication changes. Blob storage, Cosmos DB, and Service Bus continue using Entra ID managed identity.
- **CAR-006 (Azure/Microsoft SDKs)**: The Logic App MUST use an HTTP HEAD request to the uploaded blob to retrieve `Content-MD5` (the managed connector response does not reliably include it — see research.md §R1). The Python tool MUST compute MD5 from in-memory downloaded bytes via `hashlib.md5()` (stdlib) to avoid an extra API call (see research.md §R4).
- **CAR-007 (Testing Scope)**: Tests MUST cover: (1) email attachment creates record with delivery tracking fields, (2) duplicate email attachment increments `deliveryCount`, (3) content update increments `version`, (4) link-sourced document populates delivery tracking, (5) dashboard renders badges for non-SFTP records. No exhaustive cross-partition or cross-channel dedup tests.
- **CAR-008 (Logging Discipline)**: Dedup decisions (new, duplicate, update) MUST be logged with structured fields including content hash, action taken, and matched record ID.

### Key Entities

- **Intake Record (Cosmos DB `intake-records` container)**: Extended schema for email and download-link records. New fields added to email records: `contentHash` (MD5 from blob upload of primary attachment), `version` (starts at 1, incremented on content updates), `deliveryCount` (total deliveries of matching content), `deliveryHistory` (array of `{deliveredAt, contentHash, action}` entries), `lastDeliveredAt` (timestamp of most recent delivery). These fields mirror the existing SFTP delivery tracking schema defined in spec 003.
- **Attachment Path Entry (within `attachmentPaths` array)**: Extended with optional `contentMd5` field to capture per-attachment blob MD5 hashes, enabling per-attachment dedup for multi-attachment emails. Existing schema: `{path, source}`. Extended: `{path, source, contentMd5}`.
- **Delivery History Entry**: Reuses the same schema as SFTP: `{deliveredAt: ISO 8601 string, contentHash: MD5 string, action: "new" | "duplicate" | "update"}`.

## Assumptions

- The Azure Blob connector in the email Logic App returns a `Content-MD5` header (or equivalent response property) on successful blob uploads. This is the standard behavior for Azure Blob Storage.
- The Python `azure-storage-blob` SDK returns content MD5 in the upload response via `content_settings.content_md5`, which is already available in the current SDK version used by the project.
- Content-hash-based dedup is scoped per partition (sender domain + year-month). A document received in March and re-received in April would be treated as separate records. This matches the SFTP dedup scoping behavior.
- Cross-channel dedup (detecting the same document arriving via both email and SFTP) is explicitly out of scope. Each intake channel maintains independent delivery tracking.
- Legacy email records (created before this feature) will not be backfilled with delivery tracking fields. They will continue to display "—" on the dashboard until they naturally age out or are reprocessed.
- The dedup key for email attachment content is the `contentHash` (MD5) value, not the filename or email message ID. Two different emails with the same attachment content hash within the same partition are considered duplicate deliveries of the same document.
- For emails with multiple attachments, the primary `contentHash` on the email record corresponds to the first attachment. Per-attachment dedup uses the `contentMd5` field on individual `attachmentPaths` entries.
- The dedup lookup for email attachments uses a Cosmos DB query within the partition (not a point-read like SFTP), since email records do not have a stable path-based dedup key. This is acceptable given partition-scoped queries are efficient.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of newly processed email attachments have `contentHash`, `version`, `deliveryCount`, `deliveryHistory`, and `lastDeliveredAt` populated on their Cosmos DB records within one processing cycle.
- **SC-002**: When the same document content is received in two separate emails within the same partition, the system correctly identifies the duplicate and increments `deliveryCount` rather than creating a separate tracking entry, with zero false positives on content hash matching.
- **SC-003**: Operators can see version and delivery badges on the dashboard for email and download-link records within 1 minute of processing completion — the same latency as SFTP records today.
- **SC-004**: No existing SFTP delivery tracking or email intake functionality is broken by this change.
- **SC-005**: Dashboard page load time does not increase measurably (less than 100ms increase) despite rendering badges for additional record types.
