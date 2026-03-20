# Quickstart: SFTP File Disposition (Success/Failure Routing)

**Feature**: 004-sftp-file-disposition
**Date**: 2026-03-18

## What This Feature Does

Replaces the current "delete file after processing" behavior in the SFTP intake Logic App with outcome-based disposition:
- **Success** → file moves from `/in/` to `/processed/`
- **Failure** → file moves from `/in/` to `/failed/`
- **True duplicate** → file moves from `/in/` to `/processed/` (already processed)
- **Unsupported type** → file deleted from `/in/` (matching existing behavior)
- **Early failure** (e.g., blob upload) → file moves from `/in/` to `/failed/` (no Cosmos DB record)

## Prerequisites

1. Feature 003 (SFTP intake) must be deployed and working
2. The `/failed/` folder must exist on the SFTP server (create `doc-exchange/failed/` in the storage account)

## Key Changes

### 1. Logic App workflow.json

- New parameter: `sftpFailedPath` (default: `"/failed/"`)
- New Scopes: `Scope_Early_Processing` (wraps `Get_file_content` through `Compute_dedup_key`) and `Scope_Route_File` (wraps `Create_intake_record_if_new` + `Check_if_spreadsheet`). `Handle_duplicate_check` sits between them.
- New actions: `Copy_to_processed`, `Copy_to_failed`, `Copy_early_to_failed`, `Update_Cosmos_processed`, `Update_Cosmos_failed`, `Check_if_supported_type`, `Delete_unsupported_from_in`, plus disposition actions in the duplicate/error paths
- Removed: `Delete_file` (replaced by disposition paths)

### 2. Cosmos DB records

- New field: `disposition` (`"processed"` | `"failed"`)
- New field: `errorDetails` (`{ actionName, errorMessage }`, only when failed)
- No migration needed — fields are additive

### 3. SFTP server

- New folder: `/failed/` (must pre-exist)

## Testing

1. **Success path**: Place a valid CSV in `/in/` → verify it lands in `/processed/` and Cosmos record has `disposition: "processed"`
2. **Downstream failure path**: Temporarily break SharePoint credentials → place a CSV in `/in/` → verify it lands in `/failed/` and Cosmos record has `disposition: "failed"` with `errorDetails`
3. **Duplicate path**: Re-upload the same CSV → verify it lands in `/processed/` (dedup detects duplicate, still moves file)
4. **Unsupported**: Place a `.docx` in `/in/` → verify it is deleted from `/in/` (no file in `/processed/` or `/failed/`)
5. **Early failure path**: Temporarily break blob storage connectivity → place a file in `/in/` → verify it lands in `/failed/` with no Cosmos record (metadata was not computed)

## Deployment

Use the existing REST API PUT deployment pattern:
```powershell
# Same as existing deploy pattern — see deploy_updates.ps1
```
