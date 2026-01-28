# Quintet PE Email Automation - Infrastructure

## Overview

This folder contains Azure Bicep templates for deploying the email processing automation infrastructure.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           QUINTET PE EMAIL AUTOMATION                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────────────────────────┐│
│  │   M365       │     │  Logic App   │     │         Service Bus              ││
│  │   Inbox      │────▶│  (Trigger)   │────▶│  ┌─────────────────────────────┐ ││
│  │              │     │              │     │  │ email-intake                │ ││
│  └──────────────┘     └──────────────┘     │  │ classification-pending      │ ││
│                              │             │  │ human-review                │ ││
│                              │             │  │ archival-pending            │ ││
│                              ▼             │  │ processing-complete         │ ││
│                       ┌──────────────┐     │  └─────────────────────────────┘ ││
│                       │   Storage    │     └──────────────────────────────────┘│
│                       │   Account    │                    │                    │
│                       │ ┌──────────┐ │                    │                    │
│                       │ │attachments│ │                    ▼                    │
│                       │ │metadata   │ │     ┌──────────────────────────────────┐│
│                       │ │errors     │ │     │         Agent Service           ││
│                       │ └──────────┘ │     │   (Classification & Extraction) ││
│                       └──────────────┘     │         [PROBABILISTIC]          ││
│                                            └──────────────────────────────────┘│
│                                                           │                    │
│  ┌──────────────────────────────────────┐                 │                    │
│  │            Cosmos DB                 │◀────────────────┘                    │
│  │  ┌────────────┐  ┌────────────────┐  │                                      │
│  │  │  emails    │  │ classifications│  │                                      │
│  │  │ audit-logs │  │ fund-mappings  │  │                                      │
│  │  └────────────┘  └────────────────┘  │                                      │
│  └──────────────────────────────────────┘                                      │
│                       ▲                                                        │
│                       │                                                        │
│  ┌────────────────────┴─────────────────┐     ┌──────────────────────────────┐ │
│  │           Web App                    │     │       Log Analytics          │ │
│  │     (Admin & Business Dashboard)     │     │        (Monitoring)          │ │
│  │         [Entra ID Auth]              │     └──────────────────────────────┘ │
│  └──────────────────────────────────────┘                                      │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Components

| Component | Purpose | Type |
|-----------|---------|------|
| **Logic App Standard** | Triggered by new emails, extracts attachments, sends to queue | Deterministic |
| **Service Bus** | Message queues for async processing, prevents overwhelming | Deterministic |
| **Storage Account** | Stores attachments, metadata, error files | Deterministic |
| **Cosmos DB** | Processing status, classifications, audit logs | Deterministic |
| **Web App** | Admin and business user dashboard | Deterministic |
| **Log Analytics** | Centralized monitoring and diagnostics | Deterministic |
| **Agent Service** | AI classification and extraction (to be added) | **Probabilistic** |

## Queue Flow & Traffic Light System

```
                    ┌─────────────────┐
                    │  email-intake   │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  AI Classification  │
                    │   (Agent Service)   │
                    └────────┬────────┘
                             │
           ┌─────────────────┼─────────────────┐
           │                 │                 │
           ▼                 ▼                 ▼
    ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
    │ 🟢 ≥80%     │   │ 🟡 65-79%   │   │ 🔴 <65%     │
    │ Auto-process│   │ human-review│   │ manual-queue│
    └──────┬──────┘   └──────┬──────┘   └─────────────┘
           │                 │
           └────────┬────────┘
                    ▼
           ┌─────────────────┐
           │archival-pending │
           └────────┬────────┘
                    │
                    ▼
           ┌─────────────────┐
           │ SharePoint      │
           │ (Fund/Class/Event)│
           └─────────────────┘
```

## Deployment

### Prerequisites

1. Azure CLI installed and authenticated
2. Bicep CLI installed (comes with Azure CLI 2.20.0+)
3. Appropriate Azure subscription permissions

### Deploy to Development

```bash
# Login to Azure
az login

# Set subscription
az account set --subscription "<subscription-id>"

# Create resource group
az group create --name rg-quintet-dev --location westeurope

# Deploy infrastructure
az deployment group create \
  --resource-group rg-quintet-dev \
  --template-file infrastructure/main.bicep \
  --parameters infrastructure/parameters/dev.bicepparam
```

### Deploy to Production

```bash
az group create --name rg-quintet-prod --location westeurope

az deployment group create \
  --resource-group rg-quintet-prod \
  --template-file infrastructure/main.bicep \
  --parameters infrastructure/parameters/prod.bicepparam
```

## Post-Deployment Configuration

### 1. Configure Logic App Office 365 Connection

After deployment, you need to authorize the Office 365 connector:

1. Go to Azure Portal → Logic App → Workflows
2. Create a new workflow with Office 365 trigger
3. Authorize the connection with appropriate M365 credentials

### 2. Configure Entra ID (Azure AD) Authentication

1. Register an App in Azure Entra ID
2. Configure redirect URIs for the Web App
3. Update Web App settings with Client ID and Tenant ID

### 3. Grant Managed Identity Permissions

```bash
# Get Web App principal ID from deployment output
# Grant Cosmos DB access
az cosmosdb sql role assignment create \
  --account-name <cosmos-account> \
  --resource-group <rg-name> \
  --role-definition-name "Cosmos DB Built-in Data Contributor" \
  --principal-id <web-app-principal-id> \
  --scope "/"
```

## Cost Estimation (MVP - Dev Environment)

| Resource | SKU | Estimated Monthly Cost |
|----------|-----|------------------------|
| Logic App Standard | WS1 (shared with ASP) | ~€25 |
| App Service Plan | B1 | ~€12 |
| Storage Account | Standard LRS | ~€5 |
| Service Bus | Standard | ~€10 |
| Cosmos DB | Serverless | ~€5-20 (pay per request) |
| Log Analytics | Per GB | ~€2-5 |
| **Total** | | **~€60-80/month** |

## Deterministic vs. Probabilistic Steps

### Deterministic (Rule-based, predictable)
- ✅ Email trigger and attachment extraction (Logic App)
- ✅ Queue routing based on confidence thresholds
- ✅ File storage operations
- ✅ Database writes
- ✅ SharePoint archival (folder path determined by classification)

### Probabilistic (AI/ML, requires monitoring)
- 🤖 Email event type classification
- 🤖 Fund/Share class identification
- 🤖 Key data extraction (amounts, dates, identifiers)
- 🤖 Document summarization

## Scalability Notes

1. **Service Bus** handles spikes - messages are queued, not lost
2. **Cosmos DB Serverless** scales automatically with request volume
3. **Logic App Standard** can scale out workers as needed
4. For high volume: upgrade App Service Plan to P1v3+
5. Consider adding **Azure Functions** for parallel processing of queued messages

## Next Steps

1. [ ] Deploy infrastructure
2. [ ] Configure Logic App workflow with M365 trigger
3. [ ] Set up Entra ID authentication
4. [ ] Deploy Agent Service for classification
5. [ ] Build dashboard UI
6. [ ] Configure SharePoint connection
