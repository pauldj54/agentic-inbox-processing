# Email Ingestion Logic App

This Logic App workflow processes incoming emails from Microsoft 365 and routes them to the email processing pipeline.

## Workflow Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  1. Email arrives in Outlook Inbox                                          │
│                    ↓                                                         │
│  2. Initialize variables (AttachmentPaths[], AttachmentsCount=0)            │
│                    ↓                                                         │
│  3. For each attachment (if not inline):                                     │
│     - Upload to Blob Storage (/attachments/{emailId}/{filename})            │
│     - Append path to AttachmentPaths                                         │
│     - Increment AttachmentsCount                                             │
│                    ↓                                                         │
│  4. Create/Update email document in Cosmos DB (with correct counts)         │
│                    ↓                                                         │
│  5. Send message to Service Bus (intake queue)                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Files

| File | Description |
|------|-------------|
| `workflow.json` | Logic App workflow definition (parameterized) |
| `parameters.dev.json` | Development environment parameters |
| `parameters.prod.json` | Production environment parameters (create as needed) |

## Key Design Decisions

### Attachment Processing
- **Only non-inline attachments are counted** - Inline images (e.g., email signatures) are excluded
- **Attachments are stored in Blob Storage** before being counted
- **Cosmos DB insert happens AFTER the foreach loop** - This ensures `attachmentsCount` and `hasAttachments` have correct values

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

### Option 2: Azure CLI
```bash
# Update the Logic App workflow
az logic workflow create \
  --resource-group rg-docproc-dev \
  --name la-email-ingestion-dev \
  --definition @workflow.json
```

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
| `office365` | Read emails from Outlook | OAuth (user consent) |
| `documentdb` | Store email metadata | Managed Identity |
| `azureblob` | Store attachments | Managed Identity |
| `servicebus` | Queue messages for processing | Managed Identity |

## Troubleshooting

### Attachments count is 0
- Check that the foreach loop runs BEFORE the Cosmos DB insert
- Verify the `isInline` condition is correctly filtering attachments

### Emails not appearing in Cosmos DB
- Check the Logic App run history for errors
- Verify Cosmos DB connection has write permissions

### Service Bus messages not being processed
- Check the `intake` queue for messages
- Verify the AI agent is running (`python src/agents/run_agent.py`)
