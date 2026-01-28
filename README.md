# Agentic Inbox Processing

Email classification system for Private Equity (PE) lifecycle events using Azure AI Agent Service. Automatically classifies incoming emails into 11 PE categories with multi-language support (English/French).

## Requirements

### Azure Services
- **Azure AI Foundry** - AI Agent Service endpoint
- **Azure Service Bus** - Message queues for email routing
- **Azure Cosmos DB** - Email storage and audit logs
- **Azure Document Intelligence** - PDF attachment extraction (optional)

### Local Development
- Python 3.12+
- Azure CLI (authenticated: `az login`)

## Setup

1. **Clone and create virtual environment**
   ```bash
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1  # Windows
   # source .venv/bin/activate   # Linux/Mac
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   
   Copy `.env01` to `.env` or ensure `.env01` exists with:
   ```ini
   AZURE_AI_PROJECT_ENDPOINT=https://<your-ai-foundry>.services.ai.azure.com/api/projects/<project>
   AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4o
   SERVICEBUS_NAMESPACE=<your-servicebus-namespace>
   COSMOS_ENDPOINT=https://<your-cosmos>.documents.azure.com:443/
   COSMOS_DATABASE=email-processing
   DOCUMENT_INTELLIGENCE_ENDPOINT=https://<your-doc-intel>.cognitiveservices.azure.com/
   ```

## Running the Solution

### Dashboard (Web UI)

Start the FastAPI dashboard to monitor email queues and classification status:

```bash
.\.venv\Scripts\python.exe -m uvicorn src.webapp.main:app --port 8000
```

Open http://127.0.0.1:8000 in your browser.

### Agentic Workflow (Email Classifier)

**Process one email** (useful for testing):
```bash
.\.venv\Scripts\python.exe src/agents/run_agent.py --once
```

**Continuous processing** (polls queue every 30 seconds):
```bash
.\.venv\Scripts\python.exe src/agents/run_agent.py
```

**With custom settings**:
```bash
.\.venv\Scripts\python.exe src/agents/run_agent.py --max-emails 100 --wait-seconds 60
```

### Send Test Email

```bash
.\.venv\Scripts\python.exe -c "
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
import json

cred = DefaultAzureCredential()
client = ServiceBusClient('<namespace>.servicebus.windows.net', credential=cred)
sender = client.get_queue_sender('email-intake')

email = {
    'id': 'test-001',
    'subject': 'Capital Call Notice - Q1 2025',
    'from': 'fund@example.com',
    'receivedAt': '2025-01-15T10:30:00Z',
    'bodyText': 'Capital Call Amount: EUR 2,500,000. Due Date: January 30, 2025.',
    'hasAttachments': False,
    'attachments': []
}

sender.send_messages(ServiceBusMessage(json.dumps(email)))
print('Email sent!')
"
```

## Architecture

```
email-intake → [Agent: Relevance Check] → [Agent: Classification + Entity Extraction]
                        ↓                              ↓
                    discarded              ┌───────────┴───────────┐
                   (Not PE)                ↓                       ↓
                                    archival-pending         human-review
                                    (confidence ≥ 65%)       (confidence < 65%)
                                           ↓
                                    (future: archival service)
```

**Entity Extraction:** During classification, the agent extracts:
- `fund_name` - PE fund name (e.g., "Private Equity Fund XV")
- `pe_company` - Management company (e.g., "Quintet Asset Management")

## PE Categories

- Capital Call / Distribution Notice / Capital Account Statement
- Quarterly Report / Annual Financial Statement / Tax Statement
- Legal Notice / Subscription Agreement / Extension Notice
- Dissolution Notice / Unknown / Not PE Related

## Service Bus Queues

| Queue | Purpose |
|-------|---------|
| `email-intake` | Incoming emails |
| `discarded` | Non-PE emails |
| `human-review` | Low confidence (<65%) needs disambiguation |
| `archival-pending` | Ready for archival (≥65% confidence) |
