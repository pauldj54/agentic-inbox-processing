# Data Model: SFTP File Disposition (Success/Failure Routing)

**Feature**: 004-sftp-file-disposition
**Date**: 2026-03-18

## Entity Changes

### 1. Intake Record (Cosmos DB — `intake-records` container)

**Container**: `intake-records` (unchanged from feature 003)
**Partition key**: `/partitionKey` (unchanged)
**Change type**: Additive — two new optional fields, no migration required

#### Field: `disposition` (new, optional)

| Field | Type | Required | Values | Description |
|---|---|---|---|---|
| `disposition` | `string` | No | `"processed"` \| `"failed"` | SFTP folder where the original file was moved after processing |

**Notes**:
- Set to `"processed"` when the file is successfully moved to `/processed/` on the SFTP server.
- Set to `"failed"` when the file is moved to `/failed/` due to a processing error.
- `null` / absent for: email-sourced records (disposition is an SFTP-only concept), records still in progress, unsupported file types (file deleted from `/in/`), early failures (before metadata computation), and legacy records created before this feature.
- Does NOT replace or conflict with the existing `status` field. `status` tracks processing state (`received`, `archived`, `error`, etc.); `disposition` tracks the SFTP file location outcome.

#### Field: `errorDetails` (new, optional)

| Field | Type | Required | Description |
|---|---|---|---|
| `errorDetails` | `object` | No | Error information when `disposition: "failed"`. Contains `actionName` (string) and `errorMessage` (string). |

**Structure**:
```json
{
  "errorDetails": {
    "actionName": "Upload_to_SharePoint",
    "errorMessage": "The request to the Graph API failed with status 401: Unauthorized"
  }
}
```

**Notes**:
- Only populated when `disposition: "failed"`.
- `actionName` identifies the Logic App action that failed (e.g., `Upload_to_SharePoint`, `Send_to_Service_Bus`, `Create_intake_record`).
- `errorMessage` contains the error message from the failed action.
- `null` / absent when `disposition: "processed"` or when disposition is not yet set.

#### No Changes to Existing Fields

All existing fields (`id`, `partitionKey`, `intakeSource`, `status`, `originalFilename`, `fileType`, `sftpPath`, `blobPath`, `contentHash`, `version`, `deliveryCount`, `deliveryHistory`, `lastDeliveredAt`, `account`, `fund`, `docType`, `docName`, `publishedDate`, `effectiveDate`, `sharepointPath`, etc.) remain unchanged.

### 2. Logic App Parameters (workflow.json)

#### New Parameter: `sftpFailedPath`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `sftpFailedPath` | `String` | `"/failed/"` | SFTP destination folder for files that encountered processing errors |

**Notes**:
- Follows the same pattern as the existing `sftpArchivePath` parameter (default: `"/processed/"`).
- Used by the `Copy_to_failed`, `Copy_err_to_failed`, `Copy_early_to_failed`, and related SFTP copy actions.
- Must include leading and trailing slashes (e.g., `"/failed/"`).

#### Existing Parameter (unchanged): `sftpArchivePath`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `sftpArchivePath` | `String` | `"/processed/"` | SFTP destination folder for successfully processed files |

Retained as-is for backward compatibility (FR-012).
