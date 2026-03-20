# Feature Specification: SFTP File Disposition (Success/Failure Routing)

**Feature Branch**: `004-sftp-file-disposition`
**Created**: 2026-03-18
**Status**: Draft
**Input**: User description: "Replace the current delete-only behavior for SFTP files with outcome-based disposition: successfully processed files are moved from /in to /processed, failed files are moved from /in to /failed. This enables reporting to data providers on which files succeeded or failed, and supports a future re-run mechanism for failed files."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Move Successfully Processed Files to /processed (Priority: P1)

When an SFTP file completes all processing steps without errors (blob upload, Cosmos DB record creation, SharePoint upload for spreadsheets or Service Bus send for PDFs), the original file is moved from `/in/` to `/processed/` on the SFTP server. This allows operators and data providers to see which files were successfully ingested by inspecting the `/processed/` folder.

**Why this priority**: This is the primary success path and the most common outcome. Moving files to `/processed/` is the foundation for provider reporting and prevents re-processing. It replaces the current delete-only behavior, which loses the audit trail of successfully processed files.

**Independent Test**: Place a valid CSV file (matching the filename convention) into the SFTP `/in/` folder. Wait for the Logic App to process it. Verify the file no longer exists in `/in/` and now exists in `/processed/` with the same filename. Verify the Cosmos DB record shows `status: "archived"` and `disposition: "processed"`.

**Acceptance Scenarios**:

1. **Given** a valid CSV file in `/in/` and the Logic App triggers, **When** all downstream processing steps succeed (blob upload, Cosmos DB record, SharePoint upload), **Then** the file is copied from `/in/` to `/processed/` and deleted from `/in/`, and the Cosmos DB record is updated with `disposition: "processed"`.
2. **Given** a valid PDF file in `/in/` and the Logic App triggers, **When** all downstream processing steps succeed (blob upload, Cosmos DB record, Service Bus send), **Then** the file is copied from `/in/` to `/processed/` and deleted from `/in/`, and the Cosmos DB record is updated with `disposition: "processed"`.
3. **Given** a valid XLSX file in `/in/` that is a content update (same path, different content hash as an existing record), **When** all downstream processing steps succeed, **Then** the file is moved to `/processed/` following the same copy-then-delete approach.

---

### User Story 2 - Move Failed Files to /failed (Priority: P1)

When any critical processing step fails for an SFTP file (blob upload failure, Cosmos DB record creation/update failure, SharePoint upload failure, or Service Bus send failure), the original file is moved from `/in/` to `/failed/` on the SFTP server instead of being deleted or left in place. Note: the Cosmos DB duplicate-check returning HTTP 404 (file not found in database) is an **expected condition** on the success path for new files — it is a managed failure that signals "new file, proceed with processing" and MUST NOT be treated as a real failure. This makes failures immediately visible by inspecting the `/failed/` folder, enables reporting to data providers on which files did not process, and sets the stage for a future re-run mechanism.

**Why this priority**: Equally critical as the success path. Without failure routing, failed files either stay in `/in/` (causing re-trigger loops) or get deleted (losing the file permanently). Moving to `/failed/` gives clear separation and enables recovery.

**Independent Test**: Place a file into `/in/` and simulate a failure condition (e.g., use an invalid SharePoint client secret to cause an upload failure). Verify the file is moved to `/failed/`, that it no longer exists in `/in/`, and that the Cosmos DB record shows `status: "error"` and `disposition: "failed"`.

**Acceptance Scenarios**:

1. **Given** a CSV file in `/in/` and the Logic App triggers, **When** the SharePoint upload step fails, **Then** the file is copied from `/in/` to `/failed/` and deleted from `/in/`, and the Cosmos DB record is updated with `status: "error"`, `disposition: "failed"`, and the error details.
2. **Given** a PDF file in `/in/` and the Logic App triggers, **When** the Service Bus send step fails, **Then** the file is copied from `/in/` to `/failed/` and deleted from `/in/`, and the Cosmos DB record is updated with `status: "error"`, `disposition: "failed"`, and the error details.
3. **Given** a file in `/in/` and the Logic App triggers, **When** the blob upload step fails (early failure, before Cosmos DB metadata is computed), **Then** the file is copied from `/in/` to `/failed/` and deleted from `/in/`. No Cosmos DB record is created for early failures because the document ID and partition key have not been computed yet — the file in `/failed/` is the sole indicator of the failure.

---

### User Story 3 - Report File Outcomes to Data Providers (Priority: P2)

An operator can determine which files from a data provider succeeded and which ones failed by inspecting the SFTP `/processed/` and `/failed/` folders, or by querying the dashboard/Cosmos DB for records with the `disposition` field. This enables the team to report back to data providers on file processing outcomes without manual investigation.

**Why this priority**: This is the reporting value that motivates the entire feature. It depends on US1 and US2 being in place. The SFTP folders themselves serve as the immediate reporting mechanism; dashboard enhancements are a bonus.

**Independent Test**: Process a batch of files where some succeed and some fail. Verify `/processed/` contains only successful files, `/failed/` contains only failed files, and `/in/` is empty. Query Cosmos DB to confirm `disposition` values match the folder locations.

**Acceptance Scenarios**:

1. **Given** 5 files were processed (3 succeeded, 2 failed), **When** an operator lists the SFTP `/processed/` folder, **Then** exactly 3 files appear.
2. **Given** 5 files were processed (3 succeeded, 2 failed), **When** an operator lists the SFTP `/failed/` folder, **Then** exactly 2 files appear.
3. **Given** processed and failed files exist, **When** an operator queries Cosmos DB filtering by `disposition`, **Then** the records accurately reflect the outcomes matching the SFTP folder contents.

---

### Edge Cases

- What happens when the copy to `/processed/` succeeds but the subsequent delete from `/in/` fails? The file exists in both locations. The system MUST log the delete failure. The trigger watermark has already advanced past this file, so it will NOT be automatically re-triggered. Manual cleanup of `/in/` is required.
- What happens when the copy to `/failed/` itself fails (e.g., SFTP connection drop during error handling)? The file remains in `/in/`. The system MUST log this secondary failure. The trigger watermark has already advanced past this file, so it will NOT be automatically re-triggered — manual intervention (re-upload or trigger state reset) is required.
- What happens when a file with the same name already exists in `/processed/` or `/failed/`? The SFTP copy action MUST overwrite the existing file (the copy connector supports overwrite mode). This handles re-delivery of the same filename across different months.
- What happens when the `/failed/` folder does not exist on the SFTP server? The SFTP copy connector creates intermediate folders automatically on HNS-enabled storage. However, the `/failed/` folder SHOULD be pre-created as part of infrastructure setup.
- What happens for unsupported file types? They are not moved to either `/processed/` or `/failed/`. They are deleted from `/in/` after processing completes (matching existing behavior where `Delete_file` removes all files). The Cosmos DB record and blob backup provide the audit trail. The `disposition` field is not set for unsupported files.
- What happens for true duplicate files (same content hash)? They are terminated with `Cancelled` status. The original file in `/in/` MUST still be moved to `/processed/` (it was already successfully processed in a prior run).
- What happens for early failures (file download, blob upload, MD5 computation)? The file is moved to `/failed/` but NO Cosmos DB record is created because the document ID and partition key have not been computed yet. The file in `/failed/` is the sole indicator. The SFTP trigger watermark advances past the file regardless, so it will NOT be re-triggered automatically.
- What happens if the SFTP server is unreachable during early failure disposition? Both `Get_file_content` and `Copy_early_to_failed` will fail. The file stays in `/in/` with no disposition. Manual intervention is required (the trigger watermark has advanced).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: On successful processing of all downstream steps (blob upload, Cosmos DB record, and file-type-specific routing), the system MUST move the original file from `/in/` to `/processed/` on the SFTP server using a copy-then-delete approach.
- **FR-002**: On failure of **any** processing step — including early failures (file download, blob upload, MD5 computation) and downstream failures (Cosmos DB record creation/update, SharePoint upload, Service Bus send) — the system MUST move the original file from `/in/` to `/failed/` on the SFTP server using a copy-then-delete approach. For early failures (before Cosmos DB metadata is computed), no Cosmos DB record is created — the file in `/failed/` is the sole indicator. **Important**: The Cosmos DB duplicate-check action (`Check_for_duplicate`) returning HTTP 404 is an expected/managed condition for new files (green path) — it is NOT a failure and MUST NOT trigger the `/failed/` disposition.
- **FR-003**: The `/failed/` folder path MUST be configurable via a Logic App parameter (default: `/failed/`), following the same pattern as the existing `sftpArchivePath` parameter for `/processed/`.
- **FR-004**: The Cosmos DB intake record MUST include a `disposition` field with values `"processed"` or `"failed"` indicating the final SFTP folder where the file was placed.
- **FR-005**: When a file is moved to `/failed/` and Cosmos DB metadata is available (post-dedup-key computation), the Cosmos DB record MUST be updated with `status: "error"`, `disposition: "failed"`, and an `errorDetails` field containing the action name and error message of the step that failed. For early failures (before metadata computation), no Cosmos DB update is performed.
- **FR-006**: When a file is moved to `/processed/`, the Cosmos DB record MUST be updated with `disposition: "processed"`.
- **FR-007**: The move operation (copy + delete) MUST use the SFTP file ID for the delete step (not the file path) to avoid UTF-8 filename encoding issues with special characters — consistent with the current delete implementation.
- **FR-008**: The copy step MUST use the file path (`x-ms-file-path`) as the source, since the SFTP copy connector requires a literal path, not an encoded file ID.
- **FR-009**: True duplicate files (same content hash, terminated with `Cancelled` status) MUST still be moved to `/processed/` since the content was already successfully processed in a prior run.
- **FR-010**: Unsupported file types MUST NOT be moved to either `/processed/` or `/failed/`. They MUST be deleted from `/in/` after processing completes (matching existing behavior where `Delete_file` removes all files regardless of type). The Cosmos DB record and blob backup serve as the audit trail. The `disposition` field is not set.
- **FR-011**: If the copy or delete step of the file disposition itself fails, the system MUST log the failure via Logic App run history. The file remains in `/in/` but the trigger watermark has already advanced — the file will NOT be automatically re-triggered. Manual intervention (re-upload or trigger state reset) is required.
- **FR-012**: The workflow MUST maintain the existing `sftpArchivePath` parameter (default: `/processed/`) for the success path, keeping backward compatibility for deployments that already use this parameter.

### Constitution Alignment Requirements *(mandatory)*

- **CAR-001 (Code Simplicity)**: The disposition logic MUST be implemented as a scope or conditional branches within the existing SFTP Logic App workflow. Avoid creating separate Logic Apps or Azure Functions for this.
- **CAR-002 (UX Simplicity)**: No new UI surfaces needed. Operators use existing SFTP folder listing and the existing dashboard. The `disposition` field in Cosmos DB enables future dashboard filtering without requiring dashboard changes in this feature.
- **CAR-003 (Responsive Design)**: Not applicable — no UI changes in this feature.
- **CAR-004 (Dependencies)**: No new dependencies. This feature uses the existing SFTP-SSH connector (copy and delete actions) and Cosmos DB connector already in the workflow.
- **CAR-005 (Auth)**: No authentication changes. The existing SFTP-SSH connection and Cosmos DB managed identity continue to be used.
- **CAR-006 (Azure/Microsoft SDKs)**: Uses the existing Logic App SFTP-SSH managed connector for copy and delete operations.
- **CAR-007 (Testing Scope)**: Tests MUST focus on: (1) successful files land in `/processed/`, (2) failed files land in `/failed/`, (3) Cosmos DB records reflect the correct `disposition`, (4) true duplicates are moved to `/processed/`. Edge cases (copy failure, delete failure) can be verified by log inspection.
- **CAR-008 (Logging Discipline)**: All disposition outcomes (success move, failure move, secondary failures during disposition) MUST be captured in the Logic App run history and Cosmos DB records. No additional logging infrastructure needed.

### Key Entities

- **Intake Record** (Cosmos DB `intake-records` container): Extended with two new fields: `disposition` (`"processed"` or `"failed"`) indicating which SFTP folder the file was moved to, and `errorDetails` (object with `actionName` and `errorMessage`, populated only when `disposition: "failed"`). All other existing fields remain unchanged.
- **SFTP Folder Structure**: Three folders in the monitored SFTP root: `/in/` (incoming files, trigger source), `/processed/` (successfully processed files), `/failed/` (files that encountered processing errors). The `/processed/` folder already exists as an intended destination; the `/failed/` folder is new.

## Assumptions

- The SFTP copy connector works correctly on HNS-enabled storage for copy operations to `/processed/` and `/failed/`. This was validated in feature 003 for the `/processed/` path (copy + delete pattern) and documented in repo memory.
- The `/processed/` folder already exists on the SFTP server. The `/failed/` folder MUST be created before deploying this feature (either manually or as part of infrastructure setup).
- The SFTP connector's copy action automatically creates the destination file if it does not exist and overwrites if it does.
- The SFTP-SSH trigger (`onupdatedfile`) uses a polling watermark model — it tracks the last-processed file modification timestamp and only picks up files with a newer timestamp. Once a Logic App run is triggered for a file, the watermark advances past it **regardless of whether the run succeeds or fails**. Files that fail and remain in `/in/` will NOT be automatically retried on the next poll. Manual intervention (re-upload or trigger state reset) is required.
- The current `Delete_file` action runs after `Check_if_spreadsheet` on the success path and deletes ALL files from `/in/` regardless of type (including unsupported). This feature restructures the workflow to replace `Delete_file` with disposition-specific delete actions and adds two Scopes for error handling.
- The Cosmos DB duplicate-check (`Check_for_duplicate`) returns HTTP 404 for new files. This is a managed/expected failure that is part of the green path (handled by `Handle_duplicate_check` branching on 404 vs. real errors). The failure disposition logic MUST only trigger on unmanaged/unexpected errors, not on this expected 404.
- The future "re-run failed files" process is out of scope for this feature. This feature only ensures failed files are preserved in `/failed/` so that such a process can be built later. The re-run process would copy files from `/failed/` back to `/in/` for reprocessing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of successfully processed files are found in the `/processed/` folder within 2 minutes of processing completion, with no successfully processed files remaining in `/in/`.
- **SC-002**: 100% of files that encounter processing errors are found in the `/failed/` folder within 2 minutes of the error occurring, with no failed files remaining in `/in/`.
- **SC-003**: Every Cosmos DB intake record has a `disposition` field that accurately reflects the file's final SFTP location (`"processed"` or `"failed"`).
- **SC-004**: An operator can determine the success/failure status of any file by inspecting the SFTP folder structure alone, without needing to check logs or Cosmos DB.
- **SC-005**: No files are permanently lost — failed files are preserved in `/failed/` for manual investigation or future re-processing, and successful files are preserved in `/processed/` as an audit trail.
