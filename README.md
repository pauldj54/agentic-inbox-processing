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
- [Pipeline Configuration](#pipeline-configuration)
- [Troubleshooting](#troubleshooting)

## Overview

This solution provides:
- **Automated Classification**: Uses Azure AI Agent Service (GPT-4o) to classify incoming PE emails
- **Multi-step Processing**: 2-stage classification (relevance check → detailed categorization)
- **Attachment Analysis**: Extracts text from PDF attachments using Azure Document Intelligence
- **Download-Link Intake**: Detects download links in email bodies, downloads the documents, and stores them in Azure Blob Storage using the same convention as regular attachments
- **Entity Extraction**: Automatically extracts fund names, PE company names, amounts, and dates
- **Confidence-based Routing**: Routes emails to different queues based on classification confidence
- **Pipeline Configuration**: Switch between full classification pipeline and triage-only mode via environment variable
- **Web Dashboard**: Real-time monitoring of processing status, queue contents, pipeline mode indicator, and link-sourced attachment indicators
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
                                              │  Step 2: Route by Pipeline Mode │
                                              └─────────────┬───────────────────┘
                                                            │
                                         ┌──────────────────┴──────────────────┐
                                         │                                     │
                                    PIPELINE_MODE                         PIPELINE_MODE
                                      = "full"                            = "triage-only"
                                         │                                     │
                                         ▼                                     ▼
                              ┌─────────────────────┐           ┌─────────────────────────┐
                              │  Step 3: Classify    │           │  Send to triage-complete │
                              │  Step 4: Extract     │           │  queue (skip classify)   │
                              └──────────┬──────────┘           └────────────┬────────────┘
                                         │                                   │
          ┌──────────────────────────────┼────────────────────┐              ▼
          │                              │                    │   ┌─────────────────────┐
          ▼                              ▼                    ▼   │  triage-complete     │
┌─────────────────────┐   ┌─────────────────────┐  ┌────────────┐│  (for IDP / downstream)│
│  discarded          │   │  archival-pending   │  │human-review││                       │
│  (Not PE related)   │   │  (≥65% confidence)  │  │(<65% conf.)│└───────────────────────┘
└─────────────────────┘   └─────────────────────┘  └────────────┘
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
  - `triage-complete` - Triage-only mode output (for IDP / downstream systems)

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

### 5. Azure Storage Account
- Create a Storage Account (for storing downloaded attachments from email links)
- Create a blob container named `attachments`
- Note the storage account name
- Ensure your identity has `Storage Blob Data Contributor` role

### 6. Role Assignments
Ensure your Azure identity has the following roles:
- **Service Bus**: `Azure Service Bus Data Sender` and `Azure Service Bus Data Receiver`
- **Cosmos DB**: `Cosmos DB Built-in Data Contributor`
- **Document Intelligence**: `Cognitive Services User`
- **AI Foundry**: `Azure AI Developer`
- **Storage Account**: `Storage Blob Data Contributor`

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

# Azure Storage Account (for link-downloaded attachments)
STORAGE_ACCOUNT_NAME=<your-storage-account-name>

# Microsoft Graph API (optional - for direct mailbox access)
# GRAPH_CLIENT_ID=<app-registration-client-id>
# GRAPH_CLIENT_SECRET=<app-registration-client-secret>
# GRAPH_TENANT_ID=<your-tenant-id>

# Pipeline Configuration
PIPELINE_MODE=full                        # "full" (default) or "triage-only"
TRIAGE_COMPLETE_QUEUE=triage-complete      # Queue name for triage-only output
# TRIAGE_COMPLETE_SB_NAMESPACE=<external>  # Optional: external Service Bus namespace
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
- Active pipeline mode indicator (Full Pipeline / Triage Only badge)
- Emails in each queue (intake, discarded, human-review, archival-pending, triage-complete)
- Processed emails from Cosmos DB with per-email pipeline status
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

### Run Automated Tests
```bash
# Run all tests
pytest

# Run unit tests only
pytest tests/unit/

# Run integration tests only
pytest tests/integration/

# Run with verbose output
pytest -v
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
│   │       ├── link_download_tool.py  # Download-link detection & blob upload
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
├── tests/                         # Automated tests
│   ├── unit/                          # Unit tests
│   │   ├── test_link_download_tool.py     # Link download tool tests
│   │   └── test_pipeline_config.py        # Pipeline mode tests
│   └── integration/                   # Integration tests
│       └── test_link_download_flow.py     # End-to-end link download flow
├── specs/                         # Feature specifications
│   ├── 001-download-link-intake/      # Download-link intake spec
│   └── 002-pipeline-config/           # Pipeline configuration spec
├── utils/                         # Utility scripts
│   ├── diagnose.py                   # Configuration checker
│   ├── test_connectivity.py          # Connection tests
│   ├── purge_queues.py               # Queue maintenance
│   └── clear_cosmos_emails.py        # Data cleanup
├── requirements.txt               # Python dependencies
├── pyproject.toml                 # Project metadata & pytest config
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
| `triage-complete` | Triage-only mode output | `PIPELINE_MODE=triage-only` |

## PE Event Deduplication

PE events are deduplicated to prevent the same event from being created multiple times when duplicate emails arrive. A **deduplication key** (SHA256 hash, first 16 chars) is generated from these normalized fields:

| Field | Description | Normalization |
|-------|-------------|---------------|
| `pe_company` | PE firm name | Lowercase, trimmed, common suffixes removed (llc, lp, inc, corp, ltd, partners, fund) |
| `fund_name` | Fund name | Same normalization as pe_company |
| `event_type` | Type of event (Capital Call, Distribution, etc.) | Lowercase, trimmed |
| `amount` | Transaction amount (optional) | Only digits and decimal point kept |
| `due_date` | Due date (optional) | Extracted to `YYYY-MM` format (month precision) |

## Download-Link Intake

The system automatically detects download links in email bodies and downloads the referenced documents. This handles the common scenario where PE firms send emails containing links to documents hosted on portals or cloud storage instead of traditional attachments.

### How It Works
1. **URL Extraction** — Parses both plain-text and HTML email bodies for HTTP/HTTPS URLs
2. **Document Filtering** — Only attempts downloads for URLs that appear to reference documents (`.pdf`, `.docx`, `.xlsx`, `.csv`, `.pptx`, `.txt`, `.zip`); skips social media and non-document domains
3. **Download & Upload** — Downloads the document via HTTPS (with a 50 MB size limit) and uploads it to Azure Blob Storage at `attachments/{emailId}/{filename}`
4. **Record Enrichment** — Updates the Cosmos DB email record with the attachment path and sets `hasAttachments` to `true`
5. **Graceful Failures** — If a download fails (timeout, 404, auth-required, non-document content), the email is still processed normally; failures are logged for operational visibility

### Dashboard Indicators
The web dashboard shows a link icon next to attachments that were sourced from download links (vs. traditional email attachments), making it easy to identify the origin of each document.

## Pipeline Configuration

The agent supports two pipeline modes, controlled by the `PIPELINE_MODE` environment variable:

| Mode | Value | Behavior |
|------|-------|----------|
| **Full Pipeline** | `full` (default) | Relevance check → Classification → Entity extraction → Confidence-based routing |
| **Triage Only** | `triage-only` | Relevance check only → Forward to `triage-complete` queue for downstream processing |

### When to Use Each Mode

- **Full Pipeline (`full`)** — The agent handles end-to-end classification and routing. Emails are classified into PE event categories, entities are extracted, and the email is routed to `archival-pending`, `human-review`, or `discarded` based on confidence.
- **Triage Only (`triage-only`)** — The agent performs the initial relevance check (PE-related vs. not) and stops. PE-relevant emails are placed on the `triage-complete` queue for consumption by an external system (e.g., an IDP platform). Non-PE emails are still routed to `discarded`. Classification and entity extraction are skipped.

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PIPELINE_MODE` | No | `full` | `full` or `triage-only` |
| `TRIAGE_COMPLETE_QUEUE` | No | `triage-complete` | Queue name for triage-only output |
| `TRIAGE_COMPLETE_SB_NAMESPACE` | No | *(unset)* | Optional external Service Bus namespace. If unset, the primary namespace is used. |

### Integration Pattern

The recommended integration model is **pull**: the downstream system (e.g., IDP) reads from the `triage-complete` queue on your primary Service Bus namespace using shared-access or RBAC credentials. This avoids cross-namespace authentication complexity.

If push to an external namespace is needed, set `TRIAGE_COMPLETE_SB_NAMESPACE` to the target FQDN. The agent will authenticate via `DefaultAzureCredential` and includes dead-letter fallback if the external send fails.

### Cosmos DB Fields

When pipeline mode is active, each processed email record in Cosmos DB includes:

| Field | Example (`full`) | Example (`triage-only`) |
|-------|-------------------|--------------------------|
| `pipelineMode` | `"full"` | `"triage-only"` |
| `stepsExecuted` | `["relevance","classification","extraction"]` | `["relevance"]` |

### Dashboard Indicators

- A **pipeline mode badge** is displayed in the dashboard header ("Full Pipeline" or "Triage Only")
- Emails processed in triage-only mode show a "Skipped (triage-only)" label in the status column
- The `triage-complete` queue appears in the queue monitor when using the primary namespace

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
