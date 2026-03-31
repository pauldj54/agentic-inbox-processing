# Data Model: Attachment Delivery Tracking

**Feature**: 006-attachment-delivery-tracking  
**Date**: 2026-03-30

## Entity: Intake Record (Cosmos DB `intake-records` container)

The intake record schema is extended for email and download-link records. These fields already exist on SFTP records (spec 003). This feature populates the same fields on email and link-sourced records.

### New Fields on Email/Link Records

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `contentHash` | `string \| null` | No | `null` | Base64-encoded MD5 hash of the primary attachment blob content. Set from HTTP HEAD `Content-MD5` (Logic App) or `hashlib.md5()` (Python). `null` if blob upload failed to produce a hash. |
| `version` | `integer` | Yes (on new) | `1` | Document version. Starts at 1, incremented when same-partition content update detected (same filename, different hash). |
| `deliveryCount` | `integer` | Yes (on new) | `1` | Total delivery count. Incremented on every duplicate or update detection within the partition. |
| `deliveryHistory` | `array<DeliveryHistoryEntry>` | Yes (on new) | `[{initial entry}]` | Ordered log of delivery events. First entry has `action: "new"`. |
| `lastDeliveredAt` | `string (ISO 8601)` | Yes (on new) | Current UTC timestamp | Timestamp of most recent delivery event. |

### Extended Field: `attachmentPaths` Array Entry

The existing `attachmentPaths` entries gain an optional `contentMd5` field.

**Before** (current schema):
```json
{ "path": "messageId/filename.pdf", "source": "attachment" }
```

**After** (extended schema):
```json
{ "path": "messageId/filename.pdf", "source": "attachment", "contentMd5": "base64encodedMD5" }
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | `string` | Yes | Blob path relative to `attachments/` container |
| `source` | `string` | Yes | `"attachment"` or `"link"` |
| `contentMd5` | `string \| null` | No | Base64-encoded MD5 of this specific attachment's blob |

### Entity: Delivery History Entry

Reuses the same schema defined in spec 003 for SFTP.

```json
{
  "deliveredAt": "2026-03-30T14:22:00Z",
  "contentHash": "d41d8cd98f00b204e9802098ecf8427e",
  "action": "new"
}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `deliveredAt` | `string (ISO 8601)` | — | Timestamp of delivery event |
| `contentHash` | `string` | — | MD5 hash at time of delivery |
| `action` | `string` | `"new"`, `"duplicate"`, `"update"` | Type of delivery event |

### Action Semantics

| Action | Trigger Condition | Fields Modified |
|--------|-------------------|-----------------|
| `"new"` | No existing record with matching `contentHash` in partition | Create record with `version: 1`, `deliveryCount: 1` |
| `"duplicate"` | Existing record with matching `contentHash` in partition | Increment `deliveryCount`, append history, update `lastDeliveredAt`. `version` unchanged. |
| `"update"` | Existing record with same filename but different `contentHash` in partition | Increment `version` AND `deliveryCount`, update `contentHash`, append history, update `lastDeliveredAt`. |

## State Transitions

```
[No record]  --contentHash not found in partition-->  NEW (version:1, deliveryCount:1)
[Existing]   --same contentHash found-->              DUPLICATE (deliveryCount++)
[Existing]   --same filename, different hash-->       UPDATE (version++, deliveryCount++)
```

## Partition Key

No change. Uses existing `/partitionKey` format: `{sender_domain}_{YYYY-MM}` for email records, `{sftpUsername}_{YYYY-MM}` for SFTP. Dedup queries are scoped to a single partition.

## Example: Email Record with Delivery Tracking

```json
{
  "id": "AAMkAGQ2...",
  "intakeSource": "email",
  "partitionKey": "partner-firm.com_2026-03",
  "status": "received",
  "from": "ops@partner-firm.com",
  "subject": "March Capital Call Notice",
  "hasAttachments": true,
  "attachmentsCount": 1,
  "attachmentPaths": [
    {
      "path": "AAMkAGQ2.../CapitalCall_March2026.pdf",
      "source": "attachment",
      "contentMd5": "d41d8cd98f00b204e9802098ecf8427e"
    }
  ],
  "contentHash": "d41d8cd98f00b204e9802098ecf8427e",
  "version": 1,
  "deliveryCount": 2,
  "deliveryHistory": [
    { "deliveredAt": "2026-03-28T10:00:00Z", "contentHash": "d41d8cd98f00b204e9802098ecf8427e", "action": "new" },
    { "deliveredAt": "2026-03-30T14:22:00Z", "contentHash": "d41d8cd98f00b204e9802098ecf8427e", "action": "duplicate" }
  ],
  "lastDeliveredAt": "2026-03-30T14:22:00Z",
  "receivedAt": "2026-03-28T10:00:00Z",
  "emailBody": "...",
  "classification": null,
  "processedAt": null
}
```
