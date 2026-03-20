# Research: SFTP File Disposition (Success/Failure Routing)

**Feature**: 004-sftp-file-disposition
**Date**: 2026-03-18

## 1. Logic App Error Handling Pattern for Disposition Branching

### Decision
Use **two Scope actions** to cover all failure points:
1. **`Scope_Early_Processing`** wraps the early actions (`Get_file_content` through `Compute_dedup_key`). If any early action fails, the file is moved to `/failed/` without a Cosmos DB update (metadata not yet computed).
2. **`Scope_Route_File`** wraps the downstream processing actions (`Create_intake_record_if_new` + `Check_if_spreadsheet`). If any downstream action fails, the file is moved to `/failed/` with a Cosmos DB update including `errorDetails`.

Inline disposition actions are added to the existing duplicate and dedup-error terminal paths inside `Handle_duplicate_check` (which sits between the two Scopes).

### Rationale
- Logic Apps Consumption tier supports `Scope` actions as the standard try-catch pattern. A Scope groups multiple actions; if any inner action fails, the Scope status is `Failed`. Actions after the Scope can use `runAfter` with `[Succeeded]` or `[Failed]` to branch.
- **Why two Scopes instead of one**: The `Handle_duplicate_check` action contains `Terminate` actions for true duplicates and unexpected errors. `Terminate` inside a Scope kills the entire Logic App run, not just the Scope. The dedup check + handling block must be **between** the two Scopes so `Terminate_duplicate` and `Terminate_unexpected_error` work correctly.
- **Why cover early failures**: The SFTP-SSH trigger uses a polling watermark model — once a file triggers a run, the watermark advances past it regardless of run outcome. Files that fail early (e.g., blob upload failure) stay in `/in/` silently and are NEVER re-triggered. Moving them to `/failed/` gives operator visibility and enables a uniform "copy from `/failed/` back to `/in/`" recovery workflow.
- **Why no Cosmos DB update for early failures**: Before `Compute_dedup_key` completes, we have no document ID or partition key. The file in `/failed/` is the sole failure indicator. This is acceptable because the recovery workflow (re-upload from `/failed/` to `/in/`) works regardless of Cosmos DB state.
- The duplicate path (`Terminate_duplicate`) and unexpected-error path (`Terminate_unexpected_error`) already terminate the run. Adding Copy + Delete BEFORE these Terminate actions is straightforward and doesn't require restructuring.

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Single Scope around entire workflow | Terminate actions inside a Scope kill the whole run, breaking duplicate/error paths |
| Leave early failures uncovered | Trigger watermark advances past the file — file stays in `/in/` silently with no retry and no visibility |
| Per-action runAfter[Failed] handlers | Duplicates Copy_to_failed + Delete logic many times; harder to maintain |
| Variable-based outcome tracking | Logic Apps variables inside If/Scope branches add complexity; Scope status is simpler |
| Separate error-handling Logic App | Over-engineering for a simple branching need; violates CAR-001 |

### Scope Boundaries

**Inside Scope_Early_Processing:**
- `Get_file_content` — SFTP file download (could fail on SFTP timeout or file lock)
- `Generate_file_id` — Compose (unlikely to fail)
- `Parse_file_extension` — Compose (unlikely to fail)
- `Strip_file_extension` — Compose (unlikely to fail)
- `Parse_filename_parts` — Compose (unlikely to fail)
- `Upload_to_blob` — Blob storage upload (could fail on storage outage)
- `Get_blob_md5` — Blob metadata read (could fail on storage outage)
- `Compute_dedup_key` — Compose (unlikely to fail)

**Between Scopes (handled inline in Handle_duplicate_check):**
- True duplicate: `Copy_dup_to_processed` → `Delete_dup_from_in` → `Terminate_duplicate`
- Unexpected dedup error: `Copy_err_to_failed` → `Delete_err_from_in` → `Terminate_unexpected_error`

**Inside Scope_Route_File:**
- `Create_intake_record_if_new` — Cosmos DB upsert (could fail on DB errors)
- `Check_if_spreadsheet` — branches to `Upload_to_SharePoint` or `Check_if_PDF` → `Send_to_Service_Bus` or `Log_unsupported_type`

**Edge case: SFTP server unreachable during early failure disposition**: If `Get_file_content` fails because the SFTP server is down, `Copy_early_to_failed` will also fail (same server). The file stays in `/in/` with no disposition. Manual intervention is required. This is unavoidable regardless of architecture.

---

## 2. Unsupported File Type Disposition

### Decision
After `Scope_Route_File` succeeds, a condition checks whether the file extension is in the supported set (`csv`, `xlsx`, `xls`, `pdf`). Only supported types proceed to success disposition (copy to `/processed/`). Unsupported types are **deleted from `/in/`** (matching existing behavior where `Delete_file` removes all files) and the run terminates with Succeeded status. The `disposition` field is not set for unsupported files.

### Rationale
- The current `Check_if_spreadsheet` action is an If condition. For unsupported types, it takes the else path → `Check_if_PDF` → else → `Log_unsupported_type` (a Compose action that always succeeds). This means the Scope completes with `Succeeded` status even for unsupported files.
- Without this guard, unsupported files would be copied to `/processed/`, violating FR-010.
- A simple condition expression `contains(createArray('csv','xlsx','xls','pdf'), outputs('Parse_file_extension'))` reuses the already-computed extension.
- The false branch MUST delete the file from `/in/` before `Terminate_skipped`. Without this delete, unsupported files remain in `/in/` after the trigger watermark advances — they accumulate as dead files. The current workflow already deletes ALL files (including unsupported) via `Delete_file` after `Check_if_spreadsheet`.
- The Cosmos DB record and blob backup provide the audit trail for unsupported files. No `disposition` field is needed.

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Check file type before the Scope | Would require restructuring action order; extension check is already available |
| Use a Terminate(Skipped) for unsupported in the Scope | Terminate kills the entire run; can't handle disposition after |
| Set a variable `isSupported` inside routing | Variables inside nested If branches are unreliable to reference outside |

---

## 3. SFTP Copy to `/failed/` Folder on HNS Storage

### Decision
Use the same SFTP-SSH Copy action pattern proven for `/processed/` — copy by file path (`x-ms-file-path`) to `sftpFailedPath + filename`, with overwrite enabled. Then delete by file ID.

### Rationale
- The copy-then-delete pattern for `/processed/` was validated in feature 003 and documented in repo memory. The SFTP rename connector is broken on HNS-enabled storage (tried 3 approaches, all failed). Copy + Delete is the proven workaround.
- The `/failed/` path is structurally identical to `/processed/` — same SFTP server, same HNS-enabled storage account, same container root. No behavioral differences expected.
- Copy source MUST use `x-ms-file-path` (literal path), not `x-ms-file-id`. Copy destination is `sftpFailedPath + filename`.
- Delete MUST use file ID (`x-ms-file-id`) to avoid UTF-8 encoding issues with special characters in filenames.
- Overwrite is enabled to handle re-delivery of the same filename.

### Pre-requisites
- The `/failed/` folder SHOULD be pre-created on the SFTP server (`doc-exchange/failed/` in the HNS storage container). The SFTP copy connector creates intermediate folders on HNS, but explicit creation avoids first-run timing issues.

---

## 4. Cosmos DB Disposition Field Updates

### Decision
Add `disposition` and `errorDetails` fields via Cosmos DB upsert actions in the disposition paths. Use the existing `documentdb` API Connection with managed identity.

### Rationale
- The existing workflow already performs Cosmos DB upserts (Create_intake_record, Patch_delivery_count, Patch_content_update). Adding two more upsert actions (one for success disposition, one for failure disposition) follows the established pattern.
- The upsert approach (with `x-ms-documentdb-is-upsert: true`) is safe: it updates existing records or creates them if missing. This handles edge cases where Create_intake_record_if_new failed but we still want to record the disposition.
- `disposition` is a simple string field (`"processed"` or `"failed"`). No schema migration needed — Cosmos DB is schemaless.
- `errorDetails` is an object with `actionName` and `errorMessage`. In Logic Apps, the failed action's error is available via `result('Scope_Route_File')` or `actions('Upload_to_SharePoint')?['error']?['message']` depending on which action failed. Inside a Scope, `result()` returns an array of all action results — we can filter for the failed one.

### Error Details Extraction Pattern
```
// Inside the failure disposition path:
// result('Scope_Route_File') returns array of action results
// Filter for Failed status to get the action that caused the failure
@first(filter(result('Scope_Route_File'), item => item['status'] == 'Failed'))
```

This gives us the action name and error message for the `errorDetails` field.

---

## 5. True Duplicate Disposition

### Decision
Add `Copy_dup_to_processed` and `Delete_dup_from_in` actions BEFORE `Terminate_duplicate` inside the true-duplicate branch of `Handle_duplicate_check` → `Compare_content_hash` → true (same hash) path.

### Rationale
- Per FR-009, true duplicate files MUST be moved to `/processed/` — the content was already successfully processed in a prior run.
- The file was re-delivered (same path, same content hash). Leaving it in `/in/` would cause it to re-trigger indefinitely. Deleting it loses the audit trail. Moving to `/processed/` is the correct disposition.
- The copy + delete actions are inserted AFTER `Patch_delivery_count` (which updates the delivery tracking in Cosmos) and BEFORE `Terminate_duplicate` (which ends the run with `Cancelled`).
- The `disposition` field does NOT need to be set for duplicates because the original Cosmos record from the first successful processing already has `disposition: "processed"`. The `Patch_delivery_count` action increments `deliveryCount` and appends to `deliveryHistory` but leaves `disposition` unchanged.
