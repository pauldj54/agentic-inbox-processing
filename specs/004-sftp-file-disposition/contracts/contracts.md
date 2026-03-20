# Contracts: SFTP File Disposition (Success/Failure Routing)

**Feature**: 004-sftp-file-disposition
**Date**: 2026-03-18

## 1. Workflow Action Sequence (Updated)

This contract defines the Logic App workflow action sequence after this feature is implemented. Changes from the current workflow are marked with `← NEW`, `← MODIFIED`, or `← REMOVED`.

### Current Workflow (before this feature)

```
Trigger
→ Get_file_content
→ Generate_file_id
→ Parse_file_extension
→ Strip_file_extension
→ Parse_filename_parts
→ Upload_to_blob
→ Get_blob_md5
→ Compute_dedup_key
→ Check_for_duplicate
→ Handle_duplicate_check
    ├─ Succeeded (found):
    │   └─ Compare_content_hash
    │       ├─ Same hash: Patch_delivery_count → Terminate_duplicate
    │       └─ Diff hash: Patch_content_update
    └─ Failed/TimedOut:
        └─ Check_if_new_file
            ├─ 404: Log_new_file
            └─ Other: Terminate_unexpected_error
→ Create_intake_record_if_new
→ Check_if_spreadsheet
    ├─ CSV/XLS/XLSX: Upload_to_SharePoint
    └─ Else: Check_if_PDF
        ├─ PDF: Compose_Service_Bus_Message → Send_to_Service_Bus
        └─ Else: Log_unsupported_type
→ Delete_file                              ← REMOVED
→ Terminate_success                        ← REMOVED (replaced by disposition paths)
```

### Updated Workflow (after this feature)

```
Trigger
→ Scope_Early_Processing                        ← NEW (wraps early actions)
    → Get_file_content
    → Generate_file_id
    → Parse_file_extension
    → Strip_file_extension
    → Parse_filename_parts
    → Upload_to_blob
    → Get_blob_md5
    → Compute_dedup_key
→ [After Scope_Early_Processing Succeeded]
    → Check_for_duplicate
    → Handle_duplicate_check
        ├─ Succeeded (found):
        │   └─ Compare_content_hash
        │       ├─ Same hash: Patch_delivery_count
        │       │   → Copy_dup_to_processed      ← NEW
        │       │   → Delete_dup_from_in          ← NEW
        │       │   → Terminate_duplicate
        │       └─ Diff hash: Patch_content_update
        └─ Failed/TimedOut:
            └─ Check_if_new_file
                ├─ 404: Log_new_file
                └─ Other:
                    → Copy_err_to_failed          ← NEW
                    → Delete_err_from_in          ← NEW
                    → Terminate_unexpected_error   ← EXISTING (with errorDetails)
→ [After Scope_Early_Processing Failed]
    → Copy_early_to_failed                      ← NEW
    → Delete_early_from_in                      ← NEW
    → Terminate_early_failed                    ← NEW
→ Scope_Route_File                            ← NEW (wraps next 2 actions)
    → Create_intake_record_if_new
    → Check_if_spreadsheet
        ├─ CSV/XLS/XLSX: Upload_to_SharePoint
        └─ Else: Check_if_PDF
            ├─ PDF: Compose_Service_Bus_Message → Send_to_Service_Bus
            └─ Else: Log_unsupported_type
→ [After Scope_Route_File Succeeded] Check_if_supported_type  ← NEW
    ├─ Supported (csv/xlsx/xls/pdf):
    │   → Copy_to_processed                   ← NEW (replaces old Delete_file)
    │   → Update_Cosmos_processed             ← NEW
    │   → Delete_from_in                      ← NEW (same logic as old Delete_file)
    │   → Terminate_success                   ← RELOCATED
    └─ Unsupported:
        → Delete_unsupported_from_in          ← NEW (delete file from /in/)
        → Terminate_skipped                   ← NEW (run ends, no disposition)
→ [After Scope_Route_File Failed] Copy_to_failed         ← NEW
    → Update_Cosmos_failed                    ← NEW
    → Delete_from_in_on_failure               ← NEW
    → Terminate_failed                        ← NEW
```

## 2. New Logic App Actions

### Scope_Early_Processing

**Location**: Wraps all early actions from `Get_file_content` through `Compute_dedup_key`
**Runs after**: Trigger
**Type**: Scope
**Contains**: `Get_file_content`, `Generate_file_id`, `Parse_file_extension`, `Strip_file_extension`, `Parse_filename_parts`, `Upload_to_blob`, `Get_blob_md5`, `Compute_dedup_key`

**Rationale**: Consolidates early failure handling. If any early action fails, the Scope status is `Failed` and the early failure disposition path runs.

### Copy_early_to_failed

**Location**: After `Scope_Early_Processing`
**Runs after**: `Scope_Early_Processing` [Failed, TimedOut]
**Type**: ApiConnection (sftpwithssh-1 — copy file)
**Inputs**:
- Source path: `triggerOutputs()?['headers']['x-ms-file-path']`
- Destination: `concat(parameters('sftpFailedPath'), triggerOutputs()?['headers']['x-ms-file-name'])`
- Overwrite: `true`
- Get all file metadata: `true`

**Note**: Uses trigger headers (file path, file name) which are always available regardless of which early action failed. No Cosmos DB update — document ID and partition key have not been computed.

### Delete_early_from_in

**Location**: After `Scope_Early_Processing`
**Runs after**: `Copy_early_to_failed` [Succeeded]
**Type**: ApiConnection (sftpwithssh-1 — delete file)
**Inputs**:
- File ID: `triggerOutputs()['headers']['x-ms-file-id']` (encoded)
- SkipDeleteIfFileNotFoundOnServer: `false`

### Terminate_early_failed

**Runs after**: `Delete_early_from_in` [Succeeded]
**Type**: Terminate (Failed)
**Error code**: `"EarlyProcessingFailed"`
**Error message**: `@{first(filter(result('Scope_Early_Processing'), item => item['status'] == 'Failed'))['error']['message']}` (same pattern as `Scope_Route_File` extraction in research Decision 4)

### Copy_dup_to_processed

**Location**: Inside `Handle_duplicate_check` → `Compare_content_hash` → true (same hash) branch
**Runs after**: `Patch_delivery_count` [Succeeded]
**Type**: ApiConnection (sftpwithssh-1 — copy file)
**Inputs**:
- Source path: `triggerOutputs()?['headers']['x-ms-file-path']`
- Destination: `concat(parameters('sftpArchivePath'), triggerOutputs()?['headers']['x-ms-file-name'])`
- Overwrite: `true`
- Get all file metadata: `true`

### Delete_dup_from_in

**Location**: Inside `Handle_duplicate_check` → `Compare_content_hash` → true (same hash) branch
**Runs after**: `Copy_dup_to_processed` [Succeeded]
**Type**: ApiConnection (sftpwithssh-1 — delete file)
**Inputs**:
- File ID: `triggerOutputs()['headers']['x-ms-file-id']` (encoded)

### Copy_err_to_failed

**Location**: Inside `Handle_duplicate_check` → else → `Check_if_new_file` → else (not 404) branch
**Runs after**: (first action in this branch)
**Type**: ApiConnection (sftpwithssh-1 — copy file)
**Inputs**:
- Source path: `triggerOutputs()?['headers']['x-ms-file-path']`
- Destination: `concat(parameters('sftpFailedPath'), triggerOutputs()?['headers']['x-ms-file-name'])`
- Overwrite: `true`
- Get all file metadata: `true`

### Delete_err_from_in

**Location**: Inside `Handle_duplicate_check` → else → `Check_if_new_file` → else (not 404) branch
**Runs after**: `Copy_err_to_failed` [Succeeded]
**Type**: ApiConnection (sftpwithssh-1 — delete file)
**Inputs**:
- File ID: `triggerOutputs()['headers']['x-ms-file-id']` (encoded)

### Scope_Route_File

**Location**: Replaces direct `Create_intake_record_if_new` → `Check_if_spreadsheet` chain
**Runs after**: `Handle_duplicate_check` [Succeeded]
**Type**: Scope
**Contains**: `Create_intake_record_if_new`, `Check_if_spreadsheet` (with all nested actions)

**Note**: `Handle_duplicate_check` itself runs after `Scope_Early_Processing` [Succeeded] via `Check_for_duplicate`.

### Check_if_supported_type

**Location**: After `Scope_Route_File`
**Runs after**: `Scope_Route_File` [Succeeded]
**Type**: If
**Expression**: `contains(createArray('csv','xlsx','xls','pdf'), outputs('Parse_file_extension'))`
**True branch**: Copy_to_processed → Update_Cosmos_processed → Delete_from_in → Terminate_success
**False branch**: Delete_unsupported_from_in → Terminate_skipped (Succeeded, file deleted from /in/, no disposition set)

### Delete_unsupported_from_in

**Location**: Inside `Check_if_supported_type` → false branch
**Runs after**: (first action in false branch)
**Type**: ApiConnection (sftpwithssh-1 — delete file)
**Inputs**:
- File ID: `triggerOutputs()['headers']['x-ms-file-id']` (encoded)
- SkipDeleteIfFileNotFoundOnServer: `false`

**Rationale**: Matches existing behavior where `Delete_file` removes ALL files from `/in/` regardless of type. Prevents unsupported files from accumulating in `/in/` after the trigger watermark advances past them.

### Copy_to_processed

**Location**: Inside `Check_if_supported_type` → true branch
**Type**: ApiConnection (sftpwithssh-1 — copy file)
**Inputs**:
- Source path: `triggerOutputs()?['headers']['x-ms-file-path']`
- Destination: `concat(parameters('sftpArchivePath'), triggerOutputs()?['headers']['x-ms-file-name'])`
- Overwrite: `true`
- Get all file metadata: `true`

### Update_Cosmos_processed

**Runs after**: `Copy_to_processed` [Succeeded]
**Type**: ApiConnection (documentdb — upsert)
**Inputs**: Upsert the existing Cosmos DB record with:
- `disposition`: `"processed"`
- All other fields preserved from existing record (use `body('Create_intake_record')?[field]` or existing data)

**Partition key**: Same as record creation: `{sftpUsername}_{YYYY-MM}`

### Delete_from_in

**Runs after**: `Update_Cosmos_processed` [Succeeded]
**Type**: ApiConnection (sftpwithssh-1 — delete file)
**Inputs**:
- File ID: `triggerOutputs()['headers']['x-ms-file-id']` (encoded)
- SkipDeleteIfFileNotFoundOnServer: `false`

### Terminate_success

**Runs after**: `Delete_from_in` [Succeeded]
**Type**: Terminate (Succeeded)

### Copy_to_failed

**Location**: After `Scope_Route_File`
**Runs after**: `Scope_Route_File` [Failed, TimedOut]
**Type**: ApiConnection (sftpwithssh-1 — copy file)
**Inputs**:
- Source path: `triggerOutputs()?['headers']['x-ms-file-path']`
- Destination: `concat(parameters('sftpFailedPath'), triggerOutputs()?['headers']['x-ms-file-name'])`
- Overwrite: `true`
- Get all file metadata: `true`

### Update_Cosmos_failed

**Runs after**: `Copy_to_failed` [Succeeded]
**Type**: ApiConnection (documentdb — upsert)
**Inputs**: Upsert the Cosmos DB record with:
- `status`: `"error"`
- `disposition`: `"failed"`
- `errorDetails`: `{ "actionName": first(filter(result('Scope_Route_File'), item => item['status'] == 'Failed'))['name'], "errorMessage": first(filter(result('Scope_Route_File'), item => item['status'] == 'Failed'))['error']['message'] }`
- All other fields preserved

**Partition key**: Same as record creation: `{sftpUsername}_{YYYY-MM}`

### Delete_from_in_on_failure

**Runs after**: `Update_Cosmos_failed` [Succeeded]
**Type**: ApiConnection (sftpwithssh-1 — delete file)
**Inputs**:
- File ID: `triggerOutputs()['headers']['x-ms-file-id']` (encoded)
- SkipDeleteIfFileNotFoundOnServer: `false`

### Terminate_failed

**Runs after**: `Delete_from_in_on_failure` [Succeeded]
**Type**: Terminate (Failed)
**Error code**: `"ProcessingFailed"`
**Error message**: Error from failed action

### Terminate_skipped

**Location**: Inside `Check_if_supported_type` → false branch
**Runs after**: `Delete_unsupported_from_in` [Succeeded]
**Type**: Terminate (Succeeded)
**Description**: Unsupported file type — file deleted from `/in/`, no disposition set. Cosmos DB record and blob backup provide audit trail.

## 3. Removed Actions

### Delete_file (removed)

Replaced by the disposition paths (Copy_to_processed + Delete_from_in or Copy_to_failed + Delete_from_in_on_failure). The delete-only behavior is eliminated.

### Terminate_success (relocated)

Moved inside the `Check_if_supported_type` → true branch, after `Delete_from_in`.

## 4. SFTP Folder Structure

| Folder | Purpose | Pre-exists | Content |
|---|---|---|---|
| `/in/` | Incoming files (trigger source) | Yes | New/unprocessed files |
| `/processed/` | Successfully processed files | Yes | Files moved after successful processing |
| `/failed/` | Failed files | **No — must be created** | Files moved after processing errors |

The `/failed/` folder maps to `doc-exchange/failed/` in the HNS-enabled storage account `sftpprocdevizr2ch55`.
