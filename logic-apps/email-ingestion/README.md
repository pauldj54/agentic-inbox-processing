# Email Ingestion Logic App

This Logic App workflow polls a Microsoft 365 mailbox via the **Microsoft Graph API** and routes incoming emails to the processing pipeline.

## Workflow Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  1. Recurrence trigger fires every 1 minute                                 │
│                    ↓                                                         │
│  2. Initialize variables (AttachmentPaths[], AttachmentsCount=0,            │
│     RejectedAttachments[])                                                   │
│                    ↓                                                         │
│  3. HTTP GET → Graph API: fetch unread messages (up to 10, with             │
│     attachments expanded inline)                                             │
│                    ↓                                                         │
│  4. For each message (sequential):                                           │
│     a. Reset variables for this message                                      │
│     b. For each attachment (parallel, if not inline):                        │
│        - Check content-type allowlist (PDF only by default)                  │
│        - Upload allowed files to Blob Storage                                │
│        - Log rejected files to RejectedAttachments                           │
│     c. Create/Update email document in Cosmos DB                             │
│     d. Send message to Service Bus (intake queue)                            │
│     e. HTTP PATCH → Graph API: mark message as read                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Files

| File | Description |
|------|-------------|
| `workflow.json` | Logic App workflow definition (Graph API trigger) |
| `workflow.office365.json` | Archived original workflow (Office365 connector trigger) |
| `parameters.dev.json` | Development environment parameters |
| `parameters.prod.json` | Production environment parameters (create as needed) |

## Key Design Decisions

### Attachment Processing
- **Only non-inline attachments are counted** - Inline images (e.g., email signatures) are excluded
- **Attachments are stored in Blob Storage** before being counted
- **Cosmos DB insert happens AFTER the foreach loop** - This ensures `attachmentsCount` and `hasAttachments` have correct values

### Allowed Attachment Types (Security Filter)

The workflow enforces a content-type allowlist on incoming attachments. Only files matching the allowed types are uploaded to Blob Storage. Rejected attachments are logged but not stored.

**Current allowed types:** PDF only (`application/pdf` or `.pdf` extension)

Rejected attachments are recorded in the `rejectedAttachments` array on each Cosmos DB document and their count is included in the Service Bus message. The dashboard shows rejection stats in the summary cards and per-email rows.

The same allowlist is applied to **direct link downloads** processed by the AI agent. Both paths share a single configuration module: `src/agents/tools/allowed_content_types.py`.

#### Extending to Additional File Types

To allow CSV and XLSX files in addition to PDF:

1. **Logic App workflow** (`workflow.json`): In the `Check_if_allowed_type` condition, add content types to the `or` expression:
   ```json
   "@or(
     equals(items('For_each_attachment')?['contentType'], 'application/pdf'),
     endsWith(items('For_each_attachment')?['name'], '.pdf'),
     equals(items('For_each_attachment')?['contentType'], 'text/csv'),
     endsWith(items('For_each_attachment')?['name'], '.csv'),
     equals(items('For_each_attachment')?['contentType'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
     endsWith(items('For_each_attachment')?['name'], '.xlsx')
   )"
   ```
2. **Shared config** (`src/agents/tools/allowed_content_types.py`): Uncomment the CSV/XLSX entries in `ALLOWED_CONTENT_TYPES` and `ALLOWED_EXTENSIONS`. The link download tool and dashboard pick up changes automatically.

### Graph API Trigger (Replaces Office365 Connector)

The workflow uses a **Recurrence + HTTP polling** pattern instead of the Office365 API connector:

- **Why**: The Office365 connector requires interactive OAuth consent (a user must sign in via the Azure Portal). This makes the monitored mailbox impossible to configure programmatically. The Graph API with client credentials (app-only) authentication removes this dependency.
- **Polling interval**: 1 minute (configurable in the Recurrence trigger)
- **Batch size**: Up to 10 unread messages per poll (`$top=10`). At 1-minute intervals this supports ~600 emails/hour.
- **Dedup safety**: Messages are marked as read after processing + Cosmos DB upserts by message ID provide double protection against reprocessing.
- **Attachment size limit**: `$expand=attachments` returns inline content (base64) for attachments up to ~3 MB each. Larger attachments require a separate Graph API call (not yet implemented).

### Changing the Monitored Mailbox

The monitored mailbox is a parameter (`monitoredMailbox`) in `parameters.dev.json`. To change it:

1. Update `monitoredMailbox` in `parameters.dev.json`
2. Ensure the App Registration has `Mail.Read` and `Mail.ReadWrite` Application permissions for the target mailbox (admin-consented)
3. Redeploy via `deploy_updates.ps1`

### Data Flow
1. **Cosmos DB** - Stores email metadata with status tracking
2. **Blob Storage** - Stores actual attachment files
3. **Service Bus** - Triggers the AI classification agent

## Deploying Changes

### Option 1: Azure Portal (Manual)
1. Navigate to your Logic App in Azure Portal
2. Go to **Logic App Designer** → **Code view**
3. Replace the workflow definition with contents of `workflow.json`
4. Update parameter values from your environment's `parameters.*.json`
5. Save

### Option 2: Deploy Script (Recommended)
The deploy script automatically injects the Graph API client secret from Key Vault:
```powershell
.\deploy_updates.ps1
```
This fetches `graph-client-secret` from Key Vault, injects it into the workflow JSON, and deploys via `az logic workflow create`.

### Option 3: Bicep/ARM Template
The Logic App can be deployed via the existing Bicep infrastructure:
```bash
cd infrastructure
az deployment group create \
  --resource-group rg-docproc-dev \
  --template-file main.bicep \
  --parameters @parameters/dev.bicepparam
```

## API Connections Required

| Connection | Purpose | Auth Method |
|------------|---------|-------------|
| `documentdb` | Store email metadata | Managed Identity |
| `azureblob` | Store attachments | Managed Identity |
| `servicebus` | Queue messages for processing | Managed Identity |

> **Note**: The Office365 API connection is no longer used. Email reading is handled directly via Microsoft Graph API HTTP actions with `ActiveDirectoryOAuth` (client credentials).

## Prerequisites

| Requirement | Details |
|-------------|----------|
| **App Registration** | Must have `Mail.Read`, `Mail.ReadWrite`, and `Mail.Send` Application permissions (admin-consented) |
| **Key Vault secret** | `graph-client-secret` in the project Key Vault (`kv-docproc-dev-izr2ch55`) |
| **Parameters** | `graphTenantId`, `graphClientId`, `monitoredMailbox` in `parameters.dev.json` |

## Troubleshooting

### Attachments count is 0
- Check that the foreach loop runs BEFORE the Cosmos DB insert
- Verify the `isInline` condition is correctly filtering attachments

### Emails not appearing in Cosmos DB
- Check the Logic App run history for errors
- Verify Cosmos DB connection has write permissions

### Logic App trigger not firing / no runs
- Verify the App Registration has admin-consented Application permissions (`Mail.Read`, `Mail.ReadWrite`)
- Check that `graph-client-secret` in Key Vault is not expired
- Verify `monitoredMailbox` parameter matches a valid mailbox in the tenant
- Check the Logic App run history for HTTP 401/403 errors from the Graph API

### Same email processed multiple times
- The mark-as-read PATCH may be failing — check run history for the `HTTP_Mark_As_Read` action
- Cosmos DB upsert by message ID provides a safety net against duplicates

### Service Bus messages not being processed
- Check the `intake` queue for messages
- Verify the AI agent is running (`python src/agents/run_agent.py`)
