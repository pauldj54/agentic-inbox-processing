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
- Logic Apps (Consumption tier, Azure) — workflow JSON definition + Logic App managed connectors (SFTP-SSH for copy/delete, Cosmos DB for record updates) (004-sftp-file-disposition)
- Cosmos DB (`email-processing` db, `intake-records` container), SFTP server (`sftpprocdevizr2ch55`) (004-sftp-file-disposition)
- Python 3.12+ + azure-servicebus (>=7.12.0, existing), azure-identity (>=1.15.0, existing), requests (new — HTTP client for API calls), python-dotenv (existing) (005-triage-consumer)
- N/A — reads from Azure Service Bus queue, posts to external HTTP API. No local or cloud persistence. (005-triage-consumer)
- Logic Apps (Consumption tier, Azure), Python 3.12 (agent/dashboard) + Logic App managed connectors (Azure Blob, Cosmos DB), Python `azure-storage-blob`, `azure-cosmos` SDKs, FastAPI + Jinja2 (dashboard) (006-attachment-delivery-tracking)
- Cosmos DB (`email-processing` db, `intake-records` container, partitioned by `/partitionKey`), Azure Blob Storage (`stdocprocdevizr2ch55`, container `attachments`) (006-attachment-delivery-tracking)
- Python 3.12+, Azure Logic Apps (Consumption tier, workflow definition JSON) + azure-cosmos (existing), azure-storage-blob (existing), azure-identity (existing), FastAPI + Jinja2 (existing webapp) (006-attachment-delivery-tracking)
- Azure Cosmos DB (`intake-records` container, `/partitionKey` partition key). Azure Blob Storage (`attachments` container). (006-attachment-delivery-tracking)

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
- 006-attachment-delivery-tracking: Added Python 3.12+, Azure Logic Apps (Consumption tier, workflow definition JSON) + azure-cosmos (existing), azure-storage-blob (existing), azure-identity (existing), FastAPI + Jinja2 (existing webapp)
- 006-attachment-delivery-tracking: Added Python 3.12+, Azure Logic Apps (Consumption tier, workflow definition JSON) + azure-cosmos (existing), azure-storage-blob (existing), azure-identity (existing), FastAPI + Jinja2 (existing webapp)
- 006-attachment-delivery-tracking: Added Logic Apps (Consumption tier, Azure), Python 3.12 (agent/dashboard) + Logic App managed connectors (Azure Blob, Cosmos DB), Python `azure-storage-blob`, `azure-cosmos` SDKs, FastAPI + Jinja2 (dashboard)


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
