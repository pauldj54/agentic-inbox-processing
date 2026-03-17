# agentic-inbox-processing Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-02-26

## Active Technologies
- Python 3.12+ + FastAPI, azure-identity, azure-cosmos, azure-servicebus, azure-ai-agents, azure-ai-documentintelligence, azure-storage-blob, aiohttp, jinja2, python-dotenv (002-pipeline-config)
- Azure Cosmos DB (`email-processing` database, `emails` container), Azure Blob Storage (002-pipeline-config)
- Azure Cosmos DB (`email-processing` database, `intake-records` container — renamed from `emails`), Azure Blob Storage (`stdocprocdevizr2ch55`) (003-sftp-intake)
- Python 3.12+ + FastAPI, azure-identity, azure-cosmos, azure-servicebus, azure-ai-agents, azure-storage-blob, azure-ai-documentintelligence, aiohttp, jinja2, python-dotenv (003-sftp-intake)
- Azure Cosmos DB (database: `email-processing`, container: `intake-records`), Azure Blob Storage (`stdocprocdevizr2ch55`), Azure Service Bus (Standard tier), SharePoint Online (document library) (003-sftp-intake)
- Python 3.12+ (agent, dashboard), Bicep (infrastructure), JSON (Logic App workflow) + FastAPI, azure-cosmos, azure-servicebus, azure-identity, azure-storage-blob (all existing — no new packages) (003-sftp-intake)
- Azure Cosmos DB (serverless, `email-processing` database, `intake-records` container), Azure Blob Storage, SharePoint Online (003-sftp-intake)
- Logic Apps (Consumption tier, Azure), Python 3.12 (agent/dashboard) + Logic App managed connectors (SFTP-SSH, Cosmos DB, Blob, Service Bus), Graph API (SharePoint), Python Azure SDKs (003-sftp-intake)
- Cosmos DB (`email-processing` db, `intake-records` collection), Azure Blob Storage (`stdocprocdevizr2ch55`), SFTP (`sftpprocdevizr2ch55`) (003-sftp-intake)

- Python 3.12+ + FastAPI, azure-identity, azure-cosmos, azure-servicebus, azure-ai-agents, azure-ai-documentintelligence, azure-storage-blob (new), aiohttp, jinja2 (001-download-link-intake)

## Project Structure

```text
backend/
frontend/
tests/
```

## Commands

cd src; pytest; ruff check .

## Code Style

Python 3.12+: Follow standard conventions

## Recent Changes
- 003-sftp-intake: Added Logic Apps (Consumption tier, Azure), Python 3.12 (agent/dashboard) + Logic App managed connectors (SFTP-SSH, Cosmos DB, Blob, Service Bus), Graph API (SharePoint), Python Azure SDKs
- 003-sftp-intake: Added Python 3.12+ (agent, dashboard), Bicep (infrastructure), JSON (Logic App workflow) + FastAPI, azure-cosmos, azure-servicebus, azure-identity, azure-storage-blob (all existing — no new packages)
- 003-sftp-intake: Added Python 3.12+ + FastAPI, azure-identity, azure-cosmos, azure-servicebus, azure-ai-agents, azure-storage-blob, azure-ai-documentintelligence, aiohttp, jinja2, python-dotenv


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
