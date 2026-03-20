# Agentic Inbox Processing - Azure Infrastructure Requirements

## Overview

This document provides a comprehensive list of all Azure services required to deploy the Agentic Inbox Processing accelerator, including recommended SKUs for a **Proof of Concept (PoC)** targeting **~100 documents of ~1 MB / 1 page per day**. The guidance is tailored for **Financial Services Industry (FSI)** customers requiring **no public internet access** wherever possible.

---

## Azure Services Inventory

| # | Azure Service | Usage Description | PoC SKU |
|---|---------------|-------------------|---------|
| 1 | **Azure App Service Plan (Linux)** | Shared compute host for the Web App running the admin dashboard on Python 3.11. | **B1** (1 core, 1.75 GB RAM) |
| 2 | **Azure App Service (Web App)** | Hosts the admin and business user dashboard. Connects to Cosmos DB, Blob Storage, Service Bus, and Document Intelligence via Managed Identity. Secured with Entra ID Easy Auth. | Runs on B1 plan above |
| 3 | **Azure Logic Apps — Email Ingestion** | Serverless workflow triggered by new emails in an M365 mailbox via the Office 365 connector. Extracts attachments, stores them in Blob Storage, creates Cosmos DB records, and enqueues messages to Service Bus. | **Consumption** (pay per trigger/action) |
| 4 | **Azure Logic Apps — SFTP File Ingestion** | Serverless workflow that polls an external SFTP server for new files via the SFTP-SSH connector. Downloads files, deduplicates via Cosmos DB, stores in Blob Storage, and routes to Service Bus. | **Consumption** (pay per trigger/action) |
| 5 | **Azure Service Bus Namespace** | Asynchronous message broker with five queues: `intake`, `discarded`, `human-review`, `archival-pending`, `triage-complete`. Provides dead-letter support and decouples intake from AI processing. | **Standard** |
| 6 | **Azure Cosmos DB for NoSQL** | Stores intake records, classification results, audit logs, fund mappings, and pipeline configuration. Serverless capacity mode — pay per RU consumed with no provisioned throughput. | **Serverless** |
| 7 | **Azure Blob Storage (Hot tier)** | Stores email attachments (`attachments` container), extracted metadata JSON (`metadata` container), and failed items (`errors` container). Shared key access disabled; all access via Managed Identity. | **Standard_LRS, Hot** |
| 8 | **Azure Queue Storage** | Backup notification channel within the same storage account. Used as a secondary messaging path alongside Service Bus. | **Standard_LRS** (same storage account) |
| 9 | **Azure Files** | File share for Logic App content and internal state. Part of the same storage account. | **Standard_LRS** (same storage account) |
| 10 | **Azure Key Vault** | Centralized secret store for SFTP SSH private keys, SharePoint client secrets, Graph API credentials, and other sensitive configuration. RBAC authorization enabled, purge protection on. | **Standard** |
| 11 | **Azure AI Document Intelligence** | Extracts text and structure from PDF documents using the prebuilt Layout model. Used for OCR and document content extraction during the classification pipeline. Local auth disabled — Managed Identity only. | **S0** |
| 12 | **Azure Log Analytics Workspace** | Centralized logging and monitoring. Collects diagnostic logs and metrics from Logic Apps, Web App, and Service Bus. 30-day retention for PoC. | **PerGB2018** (pay per GB ingested) |
| 13 | **Azure Monitor Diagnostic Settings** | Configured on each Logic App and the Web App to stream runtime logs, HTTP logs, console logs, and metrics to the Log Analytics Workspace. | Included (no separate SKU) |
| 14 | **Microsoft Entra ID (App Registrations)** | Three app registrations: (a) Web App Easy Auth for dashboard SSO, (b) Graph API service for email attachment download, (c) SharePoint connector for document archival. | Included with Azure subscription |
| 15 | **Azure Managed Connector — Office 365** | Logic App API connection for M365 email trigger. Listens for new emails and retrieves attachments from Exchange Online. | Included with Logic App Consumption |
| 16 | **Azure Managed Connector — SFTP-SSH** | Logic App API connection for SFTP file polling and download. Authenticates with SSH private key from Key Vault. | Included with Logic App Consumption |
| 17 | **Azure Managed Connector — SharePoint Online** | Logic App API connection for archiving classified documents to a SharePoint document library. Uses Entra ID client credentials. | Included with Logic App Consumption |
| 18 | **Azure Managed Connector — Azure Blob Storage** | Logic App API connection for writing attachments and files to Blob Storage containers. Authenticates via Managed Identity. | Included with Logic App Consumption |
| 19 | **Azure Managed Connector — Azure Cosmos DB** | Logic App API connection for creating and querying intake records. Authenticates via Managed Identity. | Included with Logic App Consumption |
| 20 | **Azure Managed Connector — Azure Service Bus** | Logic App API connection for sending messages to Service Bus queues. Authenticates via Managed Identity. | Included with Logic App Consumption |

### External Dependencies (Not Azure-billed Services)

| Dependency | Description |
|------------|-------------|
| **Microsoft 365 Exchange Online Mailbox** | Source mailbox monitored by the email ingestion Logic App. Requires an M365 license with Exchange Online. |
| **Internal SFTP Server** | Self-hosted SFTP server recieving files to be ingested. Authenticated via SSH private key stored in Key Vault. |
| **SharePoint Online Document Library** | Final archival destination for classified documents, organized by fund/class/event type. Accessed via Entra ID App Registration with client credentials. |
| **Microsoft Graph API** | Used by the agent service to download email attachments programmatically from M365 mailboxes. |

---

## Private Networking — FSI Compliance

For FSI customers requiring **no public internet exposure**, each Azure service should be locked down using **Private Endpoints** and **VNet integration**. Below is the complete list of connections and how to implement private navigation.

### Network Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Azure Virtual Network                          │
│                                                                         │
│  ┌──────────────────┐   ┌─────────────────────────────────────────────┐│
│  │  Subnet: web     │   │  Subnet: private-endpoints                 ││
│  │  (VNet Integ.)   │   │                                             ││
│  │                  │   │  PE: Blob Storage                          ││
│  │  Web App ────────┼─ ▶│  PE: Queue Storage                        ││
│  │  (App Service)   │   │  PE: File Storage                         ││
│  │                  │   │  PE: Cosmos DB for NoSQL                   ││
│  └──────────────────┘   │  PE: Service Bus Namespace                ││
│                         │  PE: Key Vault                             ││
│  ┌──────────────────┐   │  PE: Document Intelligence                ││
│  │  Subnet: logic   │   │  PE: Log Analytics (via AMPLS)            ││
│  │  (VNet Integ.)   │   │                                             ││
│  │                  │   └─────────────────────────────────────────────┘│
│  │  Logic App Std ──┼──▶ (same private endpoints above)               │
│  │                  │                                                  │
│  └──────────────────┘                                                  │
└─────────────────────────────────────────────────────────────────────────┘
         │
         │  ExpressRoute / VPN Gateway
         ▼
┌─────────────────────┐     ┌─────────────────────┐
│  On-premises / FSI  │     │  External SFTP       │
│  corporate network  │     │  (via VPN/ExpressRoute│
│                     │     │   or SFTP connector)  │
└─────────────────────┘     └─────────────────────┘
```

### Service-by-Service Private Endpoint Configuration

| # | Azure Service | Private Endpoint Sub-Resource | Private DNS Zone | Notes |
|---|---------------|-------------------------------|------------------|-------|
| 1 | **Azure Blob Storage** | `blob` | `privatelink.blob.core.windows.net` | Set `networkAcls.defaultAction: Deny`. |
| 2 | **Azure Queue Storage** | `queue` | `privatelink.queue.core.windows.net` | Same storage account as Blob. |
| 3 | **Azure Files** | `file` | `privatelink.file.core.windows.net` | Same storage account as Blob. |
| 4 | **Azure Service Bus Namespace** | `namespace` | `privatelink.servicebus.windows.net` | Set `publicNetworkAccess: Disabled` on the namespace. |
| 5 | **Azure Cosmos DB for NoSQL** | `Sql` | `privatelink.documents.azure.com` | Set `publicNetworkAccess: Disabled` on the account. |
| 6 | **Azure Key Vault** | `vault` | `privatelink.vaultcore.azure.net` | Set `networkAcls.defaultAction: Deny`. Keep `bypass: AzureServices`. |
| 7 | **Azure AI Document Intelligence** | `account` | `privatelink.cognitiveservices.azure.com` | Set `publicNetworkAccess: Disabled` and `networkAcls.defaultAction: Deny`. |
| 8 | **Azure Log Analytics Workspace** | Via **Azure Monitor Private Link Scope (AMPLS)** | `privatelink.monitor.azure.com`, `privatelink.oms.opinsights.azure.com`, `privatelink.ods.opinsights.azure.com` | Requires an AMPLS resource wrapping the workspace. |
| 9 | **Azure App Service (Web App)** | **VNet Integration** (outbound) + optional **Private Endpoint** (inbound) | `privatelink.azurewebsites.net` | Use VNet Integration for outbound traffic to PEs. Add inbound PE if the dashboard must not be publicly accessible. |
| 10 | **Azure Logic Apps** | **Upgrade to Logic App Standard** with **VNet Integration** | N/A (outbound VNet integration) | See note below. |

> **Important — Logic App Consumption Limitation:** Logic App Consumption tier does **not** support VNet integration or Private Endpoints. For FSI environments requiring no public internet access, **upgrade to Logic App Standard** (WS1 SKU) running on a dedicated Windows App Service Plan with VNet integration to route all outbound traffic through private endpoints.

### Required Private DNS Zones

All Private Endpoints require Azure Private DNS Zones linked to the VNet for name resolution:

- `privatelink.blob.core.windows.net`
- `privatelink.queue.core.windows.net`
- `privatelink.file.core.windows.net`
- `privatelink.servicebus.windows.net`
- `privatelink.documents.azure.com`
- `privatelink.vaultcore.azure.net`
- `privatelink.cognitiveservices.azure.com`
- `privatelink.azurewebsites.net` (if Web App inbound PE is used)
- `privatelink.monitor.azure.com`
- `privatelink.oms.opinsights.azure.com`
- `privatelink.ods.opinsights.azure.com`

---

## RBAC Role Assignments — Minimum Privilege

All service-to-service authentication uses **Azure Managed Identity** (SystemAssigned). No shared keys or connection strings are used. Below are the minimum RBAC roles assigned to each identity.

### Logic App — Email Ingestion (Managed Identity)

| Target Resource | Azure RBAC Role | Role Definition ID | Justification |
|----------------|-----------------|-------------------|---------------|
| Azure Blob Storage | Storage Blob Data Owner | `b7e6dc6d-f1e8-4753-8033-0f276bb0955b` | Write attachments to blob containers |
| Storage Account | Storage Account Contributor | `17d1049b-9a84-46fb-8f53-869881c3d3ab` | Manage storage account configuration |
| Azure Queue Storage | Storage Queue Data Contributor | `974c5e8b-45b9-4653-ba55-5f855dd0fb88` | Send/receive queue messages |
| Azure Files | Storage File Data SMB Share Contributor | `0c867c2a-1d8c-454a-a3db-ab2ea1bdc8bb` | Access Logic App content file share |
| Azure Service Bus | Azure Service Bus Data Owner | `090c5cfd-751d-490a-894a-3ce6f1109419` | Send messages to intake queues |
| Azure Cosmos DB for NoSQL | Cosmos DB Built-in Data Contributor | `00000000-0000-0000-0000-000000000002` (SQL RBAC) | Create/update intake records |

### Logic App — SFTP File Ingestion (Managed Identity)

| Target Resource | Azure RBAC Role | Role Definition ID | Justification |
|----------------|-----------------|-------------------|---------------|
| Azure Blob Storage | Storage Blob Data Owner | `b7e6dc6d-f1e8-4753-8033-0f276bb0955b` | Write downloaded files to blob containers |
| Azure Service Bus | Azure Service Bus Data Owner | `090c5cfd-751d-490a-894a-3ce6f1109419` | Send messages to intake queues |
| Azure Cosmos DB for NoSQL | Cosmos DB Built-in Data Contributor | `00000000-0000-0000-0000-000000000002` (SQL RBAC) | Create/update records, check duplicates |

### Web App — Admin Dashboard (Managed Identity)

| Target Resource | Azure RBAC Role | Role Definition ID | Justification |
|----------------|-----------------|-------------------|---------------|
| Azure Cosmos DB for NoSQL | Cosmos DB Built-in Data Contributor | `00000000-0000-0000-0000-000000000002` (SQL RBAC) | Read/write processing records, classifications |
| Azure Blob Storage | Storage Blob Data Reader | `2a2b9908-6ea1-4ae2-8e65-a410df84e7d1` | Read attachments and metadata |
| Azure Service Bus | Azure Service Bus Data Receiver | `4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0` | Receive/peek messages from queues |
| Azure AI Document Intelligence | Cognitive Services User | `a97b65f3-24c7-4388-baec-2e87135dc908` | Call Document Intelligence Layout API for PDF extraction |

### Azure Key Vault Access

| Identity | Azure RBAC Role | Justification |
|----------|-----------------|---------------|
| Deployment principal / Admin | Key Vault Administrator | Manage secrets (SFTP key, SharePoint secret, Graph API credentials) |
| Logic Apps (runtime secret reads) | Key Vault Secrets User | Read-only access to secrets needed for API connections |

---

## Microsoft Graph API — App Registration & Minimum Scopes

The agent service uses Microsoft Graph API to download email attachments from M365 mailboxes. This requires a dedicated **Entra ID App Registration** with **Application permissions** (not delegated), since the service operates without a signed-in user.

### Required Minimum Scopes

| Permission | Type | Description | Admin Consent Required |
|------------|------|-------------|----------------------|
| `Mail.Read` | Application | Read mail in all mailboxes. Required to list and download email attachments via `/users/{id}/messages/{id}/attachments`. | Yes |

> **Least-privilege note:** If only metadata is needed (subject, sender, date) without body content, `Mail.ReadBasic.All` can be used instead. However, since the accelerator downloads attachment content (via `contentBytes`), `Mail.Read` is the minimum required scope.

### App Registration Setup

1. Register a new application in **Microsoft Entra ID → App Registrations**
2. Add **API Permission**: Microsoft Graph → Application → `Mail.Read`
3. **Grant admin consent** for the tenant
4. Create a **Client Secret** (or use a certificate for production)
5. Store the following in **Azure Key Vault**:
   - `GRAPH_CLIENT_ID` — Application (client) ID
   - `GRAPH_CLIENT_SECRET` — Client secret value
   - `GRAPH_TENANT_ID` — Directory (tenant) ID
6. The agent service authenticates using `ClientSecretCredential` with these values

### Authentication Flow

```
Agent Service → ClientSecretCredential → https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
    → scope: https://graph.microsoft.com/.default
    → GET /v1.0/users/{user}/messages/{msg}/attachments
```

---

## SharePoint Online — Document Library Connection

The accelerator archives classified documents to a **SharePoint Online document library** organized by fund, share class, and event type. The connection uses **Entra ID client credentials** (service principal).

### App Registration Setup

1. Register a new application (or reuse the Graph API app) in **Microsoft Entra ID → App Registrations**
2. Add **API Permission**: SharePoint → Application → `Sites.Selected` (preferred for least privilege) or `Sites.ReadWrite.All`
3. **Grant admin consent** for the tenant
4. Create a **Client Secret**
5. Store in **Azure Key Vault**:
   - `sharepoint-client-secret` — Client secret value
   - Parameters: `sharepointClientId`, `sharepointTenantId`, `sharepointSiteUrl`, `sharepointDocLibraryPath`

### Minimum SharePoint Permissions

| Permission | Type | Description | Recommendation |
|------------|------|-------------|----------------|
| `Sites.Selected` | Application | Access only specific SharePoint sites granted via site-level permission. Most restrictive option. | **Recommended for FSI** |
| `Sites.ReadWrite.All` | Application | Read/write all SharePoint sites. Use only if `Sites.Selected` is not feasible. | Fallback option |

### Granting `Sites.Selected` Access to a Specific Site

After granting `Sites.Selected` in the app registration, use the Graph API to grant the app write access to the target site:

```http
POST https://graph.microsoft.com/v1.0/sites/{site-id}/permissions
Content-Type: application/json

{
  "roles": ["write"],
  "grantedToIdentities": [
    {
      "application": {
        "id": "<app-registration-client-id>",
        "displayName": "Inbox Processing Accelerator"
      }
    }
  ]
}
```

### Logic App SharePoint Connector

The SFTP Logic App uses the **SharePoint Online managed connector** with client credentials flow:
- `token:clientId` — App Registration client ID
- `token:clientSecret` — Client secret from Key Vault
- `token:TenantId` — Entra ID tenant ID
- `token:grantType` — `client_credentials`

---

## SFTP Connection — SSH Key Authentication

The accelerator connects to an external SFTP server to ingest partner-delivered files. Authentication uses **SSH private key** (Ed25519 or RSA) stored securely in Azure Key Vault.

### Configuration

| Parameter | Description | Example |
|-----------|-------------|---------|
| `sftpHost` | SFTP server hostname | `sftp.partner.com` |
| `sftpPort` | SFTP server port | `22` |
| `sftpUsername` | SFTP username | `partner-reader` |
| `sftpFolderPath` | Folder to monitor for new files | `/inbox/` |
| `sftpArchiveFolderPath` | Folder to move processed files to | `/processed/` |

### SSH Key Setup

1. **Generate an Ed25519 key pair** (recommended over RSA for FSI):
   ```bash
   ssh-keygen -t ed25519 -C "inbox-processing-sftp" -f partner-reader-ed25519
   ```
2. **Provide the public key** (`partner-reader-ed25519.pub`) to the SFTP server administrator for installation in `~/.ssh/authorized_keys`
3. **Store the private key** in Azure Key Vault as a secret named `sftp-private-key`
4. The Logic App retrieves the private key at deployment time via `keyVault.getSecret('sftp-private-key')` and passes it to the SFTP-SSH managed connector

### SFTP Connector Configuration (Logic App)

The `sftpwithssh` managed connector is created with:
- `hostName` — SFTP server address
- `portNumber` — SSH port (default 22)
- `userName` — SFTP user account
- `privateKey` — SSH private key content (from Key Vault)
- `acceptAnySshHostKey` — Set to `true` for PoC; for production FSI, set to `false` and configure the known host key

> **FSI Recommendation:** For production, set `acceptAnySshHostKey: false` and pin the server's SSH host key fingerprint to prevent man-in-the-middle attacks. The SFTP server should also be accessible only via ExpressRoute or VPN, not over the public internet.

---

## Web App Authentication — Entra ID Easy Auth

The admin dashboard Web App uses **Azure App Service Easy Auth** (Authentication/Authorization) with Entra ID.

### App Registration

1. Register an application in **Microsoft Entra ID → App Registrations**
2. Set the **Redirect URI** to `https://<webapp-name>.azurewebsites.net/.auth/login/aad/callback`
3. Configure the Web App with:
   - `authClientId` — The App Registration client ID
   - `authTenantId` — The Entra ID tenant ID
4. Easy Auth enforces authentication and redirects unauthenticated users to the Entra ID login page

### Minimum Configuration

| Setting | Value |
|---------|-------|
| `requireAuthentication` | `true` |
| `unauthenticatedClientAction` | `RedirectToLoginPage` |
| `identityProvider` | `azureActiveDirectory` |
| Token store | Enabled |

---

## Summary of Entra ID App Registrations

| App Registration | Purpose | API Permissions | Auth Method |
|-----------------|---------|-----------------|-------------|
| **Web App Easy Auth** | Authenticate dashboard users via browser SSO | None (user sign-in only) | OpenID Connect (redirect) |
| **Graph API Service** | Download email attachments from M365 mailboxes | `Mail.Read` (Application) | Client credentials (`ClientSecretCredential`) |
| **SharePoint Connector** | Archive documents to SharePoint document library | `Sites.Selected` (Application) | Client credentials (Logic App connector) |
