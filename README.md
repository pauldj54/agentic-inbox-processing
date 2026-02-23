# Agentic Inbox Processing

An intelligent email classification system for Private Equity (PE) lifecycle events using Azure AI Agent Service. Automatically classifies incoming emails into 11 PE categories with multi-language support (English/French) and routes them based on classification confidence.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Azure Services Setup](#azure-services-setup)
- [Local Installation](#local-installation)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [PE Event Categories](#pe-event-categories)
- [Troubleshooting](#troubleshooting)

## Overview

This solution provides:
- **Automated Classification**: Uses Azure AI Agent Service (GPT-4o) to classify incoming PE emails
- **Multi-step Processing**: 2-stage classification (relevance check → detailed categorization)
- **Attachment Analysis**: Extracts text from PDF attachments using Azure Document Intelligence
- **Entity Extraction**: Automatically extracts fund names, PE company names, amounts, and dates
- **Confidence-based Routing**: Routes emails to different queues based on classification confidence
- **Web Dashboard**: Real-time monitoring of processing status and queue contents
- **Deduplication**: Prevents duplicate PE events using intelligent hashing

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────────────────────┐
│   Email Source  │────▶│  Logic App      │────▶│  Service Bus (email-intake)     │
│   (Outlook/API) │     │  (Ingestion)    │     └─────────────┬───────────────────┘
└─────────────────┘     └─────────────────┘                   │
                                                              ▼
                                              ┌─────────────────────────────────┐
                                              │  Email Classification Agent     │
                                              │  (Azure AI Agent Service)       │
                                              │                                 │
                                              │  Step 1: Relevance Check        │
                                              │  Step 2: Full Classification    │
                                              │  Step 3: Entity Extraction      │
                                              └─────────────┬───────────────────┘
                                                            │
                           ┌────────────────────────────────┼────────────────────────────────┐
                           │                                │                                │
                           ▼                                ▼                                ▼
              ┌─────────────────────┐        ┌─────────────────────┐        ┌─────────────────────┐
              │  discarded          │        │  archival-pending   │        │  human-review       │
              │  (Not PE related)   │        │  (≥65% confidence)  │        │  (<65% confidence)  │
              └─────────────────────┘        └─────────────────────┘        └─────────────────────┘
                                                            │
                                                            ▼
                                              ┌─────────────────────────────────┐
                                              │  Cosmos DB (Audit & Storage)    │
                                              └─────────────────────────────────┘
```

## Prerequisites

### Required Software
- **Python 3.12+** - [Download Python](https://www.python.org/downloads/)
- **Azure CLI** - [Install Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli)
- **Git** - [Install Git](https://git-scm.com/downloads)

### Azure Subscription
You need an active Azure subscription with permissions to create the required resources.

## Azure Services Setup

Create the following Azure services (or use the provided Bicep templates in `/infrastructure`):

### 1. Azure AI Foundry (AI Agent Service)
- Create an Azure AI Foundry resource
- Deploy a GPT-4o model
- Note the endpoint URL and project name

### 2. Azure Service Bus
- Create a Service Bus namespace (Standard tier or higher)
- Create the following queues:
  - `email-intake` - Incoming emails
  - `discarded` - Non-PE emails
  - `human-review` - Low confidence classifications
  - `archival-pending` - Successfully classified emails

### 3. Azure Cosmos DB
- Create a Cosmos DB account (NoSQL API)
- Create a database named `email-processing`
- Create containers:
  - `emails` (partition key: `/id`)
  - `pe-events` (partition key: `/id`)
  - `audit-logs` (partition key: `/emailId`)

### 4. Azure Document Intelligence (Optional)
- Create a Document Intelligence resource
- Note the endpoint URL (for PDF attachment processing)

### 5. Role Assignments
Ensure your Azure identity has the following roles:
- **Service Bus**: `Azure Service Bus Data Sender` and `Azure Service Bus Data Receiver`
- **Cosmos DB**: `Cosmos DB Built-in Data Contributor`
- **Document Intelligence**: `Cognitive Services User`
- **AI Foundry**: `Azure AI Developer`

### Infrastructure as Code (Optional)
Deploy all resources using Bicep:
```bash
cd infrastructure
az deployment group create \
  --resource-group <your-rg> \
  --template-file main.bicep \
  --parameters parameters/dev.bicepparam
```

## Local Installation

### Step 1: Clone the Repository
```bash
git clone <repository-url>
cd agentic-inbox-processing
```

### Step 2: Create Virtual Environment

**Windows (PowerShell)**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux/macOS**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Azure Authentication
```bash
# Login to Azure
az login

# Set your subscription (if you have multiple)
az account set --subscription "<subscription-name-or-id>"

# Verify authentication
az account show
```

## Configuration

### Step 1: Create Environment File
Create a `.env` file in the project root with the following variables:

```ini
# Azure AI Agent Service
AZURE_AI_PROJECT_ENDPOINT=https://<your-ai-foundry>.services.ai.azure.com/api/projects/<project-id>
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4o

# Azure Service Bus
SERVICEBUS_NAMESPACE=<your-servicebus-namespace>

# Azure Cosmos DB
COSMOS_ENDPOINT=https://<your-cosmos-account>.documents.azure.com:443/
COSMOS_DATABASE=email-processing

# Azure Document Intelligence (optional - for PDF attachments)
DOCUMENT_INTELLIGENCE_ENDPOINT=https://<your-doc-intel>.cognitiveservices.azure.com/

# Microsoft Graph API (optional - for direct mailbox access)
# GRAPH_CLIENT_ID=<app-registration-client-id>
# GRAPH_CLIENT_SECRET=<app-registration-client-secret>
# GRAPH_TENANT_ID=<your-tenant-id>
```

### Step 2: Validate Configuration
```bash
python utils/diagnose.py
```

This script checks:
- Environment variables are set
- Azure authentication is working
- Service Bus connectivity
- Cosmos DB connectivity

## Running the Application

### Option 1: Web Dashboard

Start the FastAPI dashboard to monitor email processing:

**Windows**
```powershell
.\.venv\Scripts\python.exe -m uvicorn src.webapp.main:app --reload --port 8000
```

**Linux/macOS**
```bash
python -m uvicorn src.webapp.main:app --reload --port 8000
```

Open your browser to: http://127.0.0.1:8000

The dashboard shows:
- Emails in each queue (intake, discarded, human-review, archival-pending)
- Processed emails from Cosmos DB
- Classification results and confidence scores

### Option 2: Email Classification Agent

**Process a single email** (best for testing):
```bash
# Windows
.\.venv\Scripts\python.exe src/agents/run_agent.py --once

# Linux/macOS
python src/agents/run_agent.py --once
```

**Continuous Processing** (polls queue every 30 seconds):
```bash
# Windows
.\.venv\Scripts\python.exe src/agents/run_agent.py

# Linux/macOS
python src/agents/run_agent.py
```

**Custom Settings**:
```bash
python src/agents/run_agent.py --max-emails 50 --wait-seconds 60
```

### Option 3: Run Both Together

Open two terminal windows:

**Terminal 1 - Dashboard:**
```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn src.webapp.main:app --reload --port 8000
```

**Terminal 2 - Agent:**
```powershell
.\.venv\Scripts\Activate.ps1
python src/agents/run_agent.py
```

## Testing

### Send a Test Email to the Queue

```python
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
import json, os
from dotenv import load_dotenv

load_dotenv('.env')
namespace = os.environ['SERVICEBUS_NAMESPACE']

cred = DefaultAzureCredential()
client = ServiceBusClient(f'{namespace}.servicebus.windows.net', credential=cred)
sender = client.get_queue_sender('email-intake')

email = {
    'id': 'test-001',
    'subject': 'Capital Call Notice - Q1 2025',
    'from': 'fund@example.com',
    'receivedAt': '2025-01-15T10:30:00Z',
    'bodyText': 'Capital Call Amount: EUR 2,500,000. Due Date: January 30, 2025. Fund: Private Equity Fund XV.',
    'hasAttachments': False,
    'attachments': []
}

sender.send_messages(ServiceBusMessage(json.dumps(email)))
print('Test email sent to email-intake queue!')
sender.close()
client.close()
```

### Check Queue Status
```bash
python src/peek_queue.py
```

### Run Connectivity Tests
```bash
python utils/test_connectivity.py
```

## Project Structure

```
agentic-inbox-processing/
├── src/
│   ├── agents/                    # Email classification agents
│   │   ├── email_classifier_agent.py  # Main classification logic
│   │   ├── classification_prompts.py  # LLM prompts and schemas
│   │   ├── run_agent.py               # CLI runner script
│   │   └── tools/                     # Agent tools
│   │       ├── cosmos_tools.py        # Cosmos DB operations
│   │       ├── document_intelligence_tool.py  # PDF extraction
│   │       ├── graph_tools.py         # Microsoft Graph API
│   │       └── queue_tools.py         # Service Bus operations
│   └── webapp/                    # FastAPI web dashboard
│       ├── main.py                    # Dashboard application
│       └── templates/                 # HTML templates
│           └── dashboard.html
├── infrastructure/                # Azure Infrastructure as Code
│   ├── main.bicep                    # Main deployment template
│   ├── modules/                      # Bicep modules
│   └── parameters/                   # Environment parameters
├── logic-apps/                    # Logic App workflows
│   └── email-ingestion/
├── utils/                         # Utility scripts
│   ├── diagnose.py                   # Configuration checker
│   ├── test_connectivity.py          # Connection tests
│   ├── purge_queues.py               # Queue maintenance
│   └── clear_cosmos_emails.py        # Data cleanup
├── requirements.txt               # Python dependencies
├── startup.sh                     # Azure App Service startup
├── gunicorn.conf.py              # Production server config
└── README.md                      # This file
```

## PE Event Categories

The system classifies emails into these Private Equity lifecycle events:

| Category | Description |
|----------|-------------|
| Capital Call | Request for committed capital from investors |
| Distribution Notice | Distribution of proceeds to investors |
| Capital Account Statement | Periodic account balance statement |
| Quarterly Report | Quarterly fund performance report |
| Annual Financial Statement | Year-end financial statements |
| Tax Statement | K-1 or tax-related documents |
| Legal Notice | Legal communications and notices |
| Subscription Agreement | New subscription documents |
| Extension Notice | Fund term extension notices |
| Dissolution Notice | Fund wind-down notifications |
| Not PE Related | Non-PE email (routed to discarded) |

## Service Bus Queues

| Queue | Purpose | Routing Condition |
|-------|---------|-------------------|
| `email-intake` | Incoming emails from Logic App | Entry point |
| `discarded` | Non-PE related emails | Not PE Related classification |
| `archival-pending` | Successfully classified emails | Confidence ≥ 65% |
| `human-review` | Uncertain classifications | Confidence < 65% |

## PE Event Deduplication

PE events are deduplicated to prevent the same event from being created multiple times when duplicate emails arrive. A **deduplication key** (SHA256 hash, first 16 chars) is generated from these normalized fields:

| Field | Description | Normalization |
|-------|-------------|---------------|
| `pe_company` | PE firm name | Lowercase, trimmed, common suffixes removed (llc, lp, inc, corp, ltd, partners, fund) |
| `fund_name` | Fund name | Same normalization as pe_company |
| `event_type` | Type of event (Capital Call, Distribution, etc.) | Lowercase, trimmed |
| `amount` | Transaction amount (optional) | Only digits and decimal point kept |
| `due_date` | Due date (optional) | Extracted to `YYYY-MM` format (month precision) |

## Troubleshooting

### Common Issues

**1. Authentication Errors**
```
Azure authentication failed
```
Solution:
```bash
az login
az account show  # Verify correct subscription
```

**2. Missing Environment Variables**
```
KeyError: 'SERVICEBUS_NAMESPACE'
```
Solution: Ensure your `.env` file exists and contains all required variables.

**3. Service Bus Connection Issues**
```
Unable to connect to Service Bus
```
Solution: Verify your Azure identity has `Azure Service Bus Data Sender/Receiver` role.

**4. Cosmos DB Access Denied**
```
Authorization token not valid
```
Solution: Assign `Cosmos DB Built-in Data Contributor` role to your identity.

### Diagnostic Commands

```bash
# Check environment configuration
python utils/diagnose.py

# Test Azure connectivity
python utils/test_connectivity.py

# View queue contents
python src/peek_queue.py
```

### Logs

The agent logs to stdout. For detailed logging:
```bash
python src/agents/run_agent.py 2>&1 | tee agent.log
```

## Contributing

1. Create a feature branch
2. Make your changes
3. Run tests to ensure nothing is broken
4. Submit a pull request

## License

Copyright © 2025. All rights reserved.
