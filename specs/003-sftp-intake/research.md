# Research: SFTP File Intake Channel

**Feature**: 003-sftp-intake
**Date**: 2026-03-09 (updated after clarification round 2)

## 1. Logic App SFTP-SSH Managed Connector

### Decision
Use the Azure Logic App SFTP-SSH managed connector with the "When files are added or modified" trigger and SSH private key authentication.

### Rationale
- The SFTP-SSH connector is a built-in Logic App managed connector — no custom code or new dependencies needed.
- Supports SSH private key authentication via the API Connection `parameterValues` at provisioning time. The private key is stored in Azure Key Vault and retrieved at Bicep deployment time via `getSecret()`.
- The "When files are added or modified" trigger provides polling-based detection with configurable interval (default 1 minute), sufficient for SC-001 (< 2 min processing).
- Supports "Get file content" action for downloading files after trigger fires.
- Supports "Rename file" action for moving processed files to `/processed/` archive folder (FR-014).
- File metadata (name, path, size, last modified) is available directly from the trigger output — no extra API calls needed.

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Azure Function with SSH.NET library | Adds code complexity and a new compute resource; Logic App connector is simpler (CAR-001) |
| Azure Data Factory SFTP connector | Over-engineering for simple file pickup; Logic Apps are already in the architecture |
| Custom Python SFTP polling (paramiko) | Adds a Python dependency and requires managing polling, retries, and connection lifecycle |
| Logic App Standard (stateful workflows) | Consumption tier is sufficient; no need for stateful execution |

### Trigger Configuration
- **Trigger**: `When files are added or modified (properties only)` → followed by `Get file content`
- **Polling interval**: 1 minute (configurable via parameter)
- **Folder path**: Configurable via Logic App parameter (e.g., `/inbox/`)
- **Include subfolders**: No (single folder monitoring)
- **File content transfer**: Chunked for files > 30 MB (connector handles automatically)

---

## 2. SFTP-SSH API Connection Provisioning (SSH Private Key)

### Decision
Provision the `sftpWithSsh` API Connection as a `Microsoft.Web/connections` Bicep resource. Authenticate using SSH private key stored in Azure Key Vault, retrieved at deployment time via `getSecret()`.

### Rationale
- The SFTP-SSH connector requires an API Connection resource that holds connection credentials. This is the same pattern used by existing connections (`azureblob`, `documentdb`, `servicebus`) in the project.
- SSH private key is the only viable authentication method for external SFTP servers — Entra ID/managed identity is not applicable for non-Azure endpoints.
- Storing the private key in Key Vault and referencing via Bicep `getSecret()` keeps secrets out of source control and parameter files. The Key Vault secret is pre-provisioned by the infrastructure/security team.
- The API Connection resource is created alongside the Logic App in the `sftp-logic-app.bicep` module.

### Bicep Resource Pattern

```bicep
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource sftpConnection 'Microsoft.Web/connections@2018-07-01-preview' = {
  name: 'sftpWithSsh'
  location: location
  properties: {
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'sftpwithssh')
    }
    displayName: 'SFTP-SSH Connection'
    parameterValues: {
      hostName: sftpHost
      portNumber: sftpPort
      userName: sftpUsername
      privateKey: keyVault.getSecret('sftp-private-key')
      acceptAnySshHostKey: true  // or configure known host key
    }
  }
}
```

### Required Pre-Provisioned Secrets
| Secret Name | Description | Owner |
|---|---|---|
| `sftp-private-key` | SSH private key (PEM format) for SFTP server authentication | Infrastructure/security team |

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Inline private key in Bicep parameters | Insecure — private key stored in plaintext in source control |
| Managed identity on SFTP server | Not applicable — external SFTP server doesn't support Azure Entra ID |
| Password-based SFTP auth | Less secure than key-based auth; not aligned with security best practices |

---

## 3. File Type Routing Strategy

### Decision
Route files by extension detected in the Logic App workflow using a `Switch` action on file extension. CSV/Excel → parse filename metadata → upload directly to SharePoint (no Service Bus). PDF → parse filename metadata → `email-intake` queue for agent processing (classification or triage-only).

### Rationale
- File extension is available from the SFTP trigger output (`triggerBody()?['Name']`) — no content-type sniffing needed.
- CSV/Excel files bypass the agent entirely (FR-005) and are uploaded directly to SharePoint by the Logic App (FR-018). They don't need classification or the `archival-pending` Service Bus queue. The Logic App handles the full lifecycle: download → parse metadata → blob backup → Cosmos DB → SharePoint upload → archive on SFTP.
- PDF files need classification (or triage-only routing), which is handled by the existing Python agent that processes the `email-intake` queue. Reusing the same queue and agent pipeline avoids duplicating classification logic (CAR-001).
- All file types have filename metadata parsed (FR-017) regardless of routing destination. Parsed metadata is stored in Cosmos DB.
- The agent (`process_next_email`) needs minor adaptation to handle `intakeSource: "sftp"` records that lack email metadata (no `from`, `subject`, `emailBody`).
- Unsupported file types are logged and skipped in the Logic App — they never reach Service Bus or SharePoint.

### File Extension Mapping
| Extension | Route | Destination | Processing |
|---|---|---|---|
| `.csv` | Direct (Logic App) | SharePoint document library | Filename metadata parsed → SharePoint upload |
| `.xlsx`, `.xls` | Direct (Logic App) | SharePoint document library | Filename metadata parsed → SharePoint upload |
| `.pdf` | Via agent | `email-intake` queue | Classification (full) or triage-only |
| Other | Skip | N/A | Logged as unsupported, file left in SFTP |

---

## 4. Cosmos DB Container Rename: `emails` → `intake-records`

### Decision
Rename the Cosmos DB container from `emails` to `intake-records`. Migrate existing data and update all code references.

### Rationale
- The container name `emails` is misleading when it also stores SFTP-sourced records. User explicitly chose rename (clarification Q2).
- Cosmos DB does not support renaming a container in-place. Migration requires: (1) create new container `intake-records` with same partition key + indexing policy, (2) copy all documents from `emails` to `intake-records`, backfilling `intakeSource: "email"` on each, (3) update all code references, (4) delete old container.
- The partition key changes from `/status` to `/partitionKey` — a composite `{source_identifier}_{YYYY-MM}` value. See §6 for full rationale.

### Migration Strategy
1. **Infrastructure (Bicep)**: Update `cosmos-db.bicep` to create `intake-records` container instead of `emails`.
2. **Data migration script**: One-time Python script to copy documents from `emails` to `intake-records`, adding `intakeSource: "email"` and computing `partitionKey` (= `{sender_domain}_{YYYY-MM}` from `from` field + `receivedAt`) for each document.
3. **Code references**: Update `CONTAINER_EMAILS` constant in `cosmos_tools.py`, container reference in `webapp/main.py`, and Logic App `workflow.json` Cosmos DB action.
4. **Backward compatibility**: During migration, both containers may coexist briefly. The migration script should be idempotent.

### References to Update
| File | Reference | Change |
|---|---|---|
| `src/agents/tools/cosmos_tools.py` | `CONTAINER_EMAILS = "emails"` | → `CONTAINER_INTAKE_RECORDS = "intake-records"` |
| `src/webapp/main.py` | `get_container_client("emails")` | → `get_container_client("intake-records")` |
| `logic-apps/email-ingestion/workflow.json` | Cosmos DB action `colls` path segment | → `intake-records` |
| `infrastructure/modules/cosmos-db.bicep` | Container resource `name: 'emails'` | → `name: 'intake-records'` |
| `tests/integration/test_flow.py` | Container references | → `intake-records` |
| `tests/integration/test_link_download_flow.py` | Container references | → `intake-records` |
| `tests/unit/test_pipeline_config.py` | Container references (if any) | → `intake-records` |

---

## 5. SFTP-Sourced Record in the Classification Agent

### Decision
Adapt the existing `process_next_email()` method to detect `intakeSource: "sftp"` records and handle them without email-specific metadata. No new processing method needed.

### Rationale
- The `email-intake` queue receives both email and SFTP-sourced messages. The agent already pulls from this queue and processes the next message.
- SFTP-sourced PDF records contain `intakeSource: "sftp"`, `fileType: "pdf"`, `originalFilename`, `blobPath`, but no `from`, `subject`, `emailBody` fields.
- The relevance check (Step 1) and classification (Step 2) prompts need to be adapted: for SFTP records, skip email-body-based analysis and process based on the attachment content only.
- The link download step (Step 1.5) should be skipped entirely for SFTP records — SFTP files are already downloaded.
- Pipeline mode handling (triage-only vs full) works identically for both sources.

### Agent Adaptation Points
1. **Detect intake source**: Check `email_data.get("intakeSource") == "sftp"` early in `process_next_email()`.
2. **Skip link download**: Wrap Step 1.5 in an `if intakeSource != "sftp"` guard.
3. **Adapt prompts**: For SFTP records, the relevance check uses only attachment content (no email subject/body). Classification uses `originalFilename` + blob content.
4. **Skip email-specific overrides**: The PE-keyword override logic in subject/body is skipped for SFTP records.

---

## 6. Duplicate Detection Strategy (Revised 2026-03-17)

### Decision
Use a **two-layer dedup** approach: path-based point-read for initial detection, content hash (blob MD5) for distinguishing true duplicates from content updates. Dedup occurs **after** blob upload.

### Dedup Key & Cosmos Document ID
The dedup key is `base64(sftpPath)` (path only, no etag). This key is used as the **Cosmos DB document id** — not the generated `sftp-{guid}`. This enables O(1) point-reads for dedup checks.

**Previous approach (rejected)**: `base64(concat(path, '|', etag))` — failed because SFTP etag contains a timestamp component (`timestamp|filesize`) that changes on every re-upload, causing false negatives (same file treated as new).

### Content Hash Source
The content hash comes from `body('Upload_to_blob')?['ContentMD5']` — the MD5 that Azure Blob Storage automatically computes on upload. This is:
- **Free**: No additional compute or libraries needed.
- **SFTP-agnostic**: Hashes raw file content, not SFTP metadata.
- **Already available**: The `Upload_to_blob` action already exists in the workflow.

### Flow Order Change
The blob upload (`Upload_to_blob`) must happen **before** the dedup check so that the content hash is available for comparison. The new flow order is:

1. Trigger → Get file content → Generate IDs → Upload_to_blob
2. Compute_dedup_key (`base64(sftpPath)`)
3. Check_for_duplicate (point-read with dedup key as doc id, partition key `"sftp"`)
4. **3-way routing**:
   - **New file** (404): Create Cosmos record → downstream processing
   - **True duplicate** (200, same contentHash): Increment `deliveryCount`, append to `deliveryHistory` → skip downstream
   - **Content update** (200, different contentHash): Update `contentHash`, increment `version`, append to `deliveryHistory` → re-run downstream processing

### Partition Key Change: `/status` → `/partitionKey`
The partition key must change from `/status` to `/partitionKey` because:
- Point-read dedup requires knowing the partition key value at read time.
- With `/status`, when a document's status changes from `"received"` to `"classified"`, the dedup check (which always reads with partition `"received"`) would miss existing records — a false negative.
- `/partitionKey` uses a composite `{source_identifier}_{YYYY-MM}` format — for emails: `{sender_domain}_{YYYY-MM}` (e.g., `partner-pe-firm.com_2026-03`); for SFTP: `{sftp_username}_{YYYY-MM}` (e.g., `sftp-partner-A_2026-03`). This value is **stable**: it never changes after creation, and provides natural tenant-per-month data distribution.
- Dedup point-reads for SFTP use the known SFTP username + current year-month as partition key. Note: dedup is scoped to the current month; cross-month re-deliveries are treated as new files.
- This requires **recreating the Cosmos DB container** (or creating a new one + migrating data), as Cosmos DB does not support partition key changes on existing containers.

### 3-Way Routing Logic

| Scenario | Check_for_duplicate Result | contentHash Match | Action |
|---|---|---|---|
| New file | 404 (Failed) | N/A | Create Cosmos record with `version: 1`, `deliveryCount: 1` → process downstream |
| True duplicate | 200 (Succeeded) | Same | Patch: increment `deliveryCount`, append `deliveryHistory` entry → Terminate (Cancelled) |
| Content update | 200 (Succeeded) | Different | Patch: update `contentHash`, increment `version` + `deliveryCount`, append `deliveryHistory` → re-process downstream |

### Delivery Tracking Fields (New)
| Field | Type | Description |
|---|---|---|
| `contentHash` | `string` | MD5 from `body('Upload_to_blob')?['ContentMD5']` |
| `version` | `number` | Starts at 1, incremented on content updates |
| `deliveryCount` | `number` | Total times this file path was delivered (including duplicates) |
| `deliveryHistory` | `object[]` | Array of `{deliveredAt, contentHash, action}` entries |
| `lastDeliveredAt` | `string` (ISO 8601) | Timestamp of most recent delivery |

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Path + etag dedup key | Etag contains timestamp, changes on re-upload → false negatives |
| Inline JavaScript MD5 | Logic App Consumption tier has 5-second timeout on inline code |
| Cosmos DB UDF for hashing | 2MB document size limit, adds complexity |
| Path-only dedup (no hash) | Cannot distinguish true duplicates from content updates |
| External dedup service | Over-engineering for this volume |
| Keep `/status` partition key | Breaks point-read dedup when status changes (false negatives) |

---

## 7. Dashboard intakeSource Extension

### Decision
Add a "Source" column to the dashboard table showing "Email" or "SFTP" based on the `intakeSource` field. Default to "Email" for records without `intakeSource` (backward compatibility during migration).

### Rationale
- FR-016 requires SFTP-sourced documents to appear alongside email documents with a clear source indicator.
- CAR-002 mandates minimal dashboard changes — a single column/badge is sufficient.
- Records without `intakeSource` field are legacy email records (pre-migration) and should show "Email" by default.
- For SFTP records, display additional fields: `originalFilename`, `fileType` in the details section.

### Dashboard Changes
| Change | Location | Detail |
|---|---|---|
| Source column | Table header + row | Badge: "Email" (blue) / "SFTP" (green) |
| Filename display | Row detail section | Show `originalFilename` for SFTP records instead of `subject` |
| File type indicator | Row detail section | Show `fileType` badge for SFTP records |
| SharePoint path | Row detail section | Show `sharepointPath` for CSV/Excel SFTP records |

---

## 8. SharePoint Connector for CSV/Excel Upload

> **⚠️ SUPERSEDED**: This decision was revised during implementation. The SharePoint managed connector does not support service principal authentication in the Logic App Consumption tier. The actual implementation uses **HTTP actions with Microsoft Graph API** (`PUT /drives/{driveId}/root:/{path}:/content` with `ActiveDirectoryOAuth`). See contracts.md §3 step 11a for the implemented approach.

### Decision
Use the Logic App built-in SharePoint managed connector ("Create file" action) to upload CSV/Excel files directly to a SharePoint document library with structured folder paths. No Service Bus intermediary for CSV/Excel.

### Rationale
- The business requirement specifies that CSV/Excel files must be archived to a SharePoint document library with folder structure `{root}/{first letter of Account}/{Account}/{Fund}/{filename}`.
- The Logic App SharePoint connector is a built-in managed connector — no new code packages or compute resources needed (CAR-004).
- Direct Logic App → SharePoint avoids unnecessary hops through Service Bus and an external consumer. The Logic App can handle the full lifecycle (download → parse → store → upload → archive) in a single workflow run.
- The SharePoint connector supports automatic folder creation via the "Create file" action's path parameter — if intermediate folders don't exist, the connector creates them.
- SharePoint site URL and root document library path are configurable via Logic App parameters for environment-specific deployments.

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Service Bus queue → external SharePoint uploader | Adds unnecessary hop; Logic App can handle upload directly |
| Microsoft Graph API from Python agent | Over-engineering; Logic App connector is simpler and already in the workflow |
| Azure Blob Storage only (no SharePoint) | Doesn't meet business requirement for SharePoint-based document management |
| Power Automate flow for SharePoint upload | Extra tooling; Logic App connector handles this natively |

### Configuration Parameters
| Parameter | Description | Example |
|---|---|---|
| `sharepointSiteUrl` | SharePoint Online site URL | `https://contoso.sharepoint.com/sites/pe-docs` |
| `sharepointDocLibraryPath` | Root document library path | `Documents` |

### Folder Path Construction
```
{sharepointDocLibraryPath}/{first letter of account}/{account}/{fund}/{filename}
```

---

## 9. SharePoint API Connection Provisioning (Entra ID App Registration)

> **⚠️ SUPERSEDED**: The `sharepointonline` API Connection is NOT used. SharePoint uploads use HTTP actions with Graph API and `ActiveDirectoryOAuth`. The SharePoint credentials (`sharepointClientId`, `sharepointClientSecret`, `sharepointTenantId`, `sharepointDriveId`) are Logic App workflow parameters, not API Connection resources. See contracts.md §3 for the implemented approach.

### Decision
Provision the `sharepointonline` API Connection as a `Microsoft.Web/connections` Bicep resource. Authenticate using an Entra ID app registration with application permissions (`Sites.ReadWrite.All`) and a client secret stored in Azure Key Vault.

### Rationale
- The Logic App Consumption tier SharePoint connector does not support managed identity for application-level (unattended) file operations. A service principal with application permissions is required for background uploads without interactive sign-in.
- `Sites.ReadWrite.All` application permission grants the service principal write access to SharePoint sites — the minimum scope needed for file creation.
- The client secret is stored in Azure Key Vault and retrieved at Bicep deployment time via `getSecret()`, keeping it out of source control.
- The Entra ID app registration and its client secret are pre-provisioned by the infrastructure/security team before deployment (per spec assumption).

### Bicep Resource Pattern

```bicep
resource sharepointConnection 'Microsoft.Web/connections@2018-07-01-preview' = {
  name: 'sharepointonline'
  location: location
  properties: {
    api: {
      id: subscriptionResourceId('Microsoft.Web/locations/managedApis', location, 'sharepointonline')
    }
    displayName: 'SharePoint Online Connection'
    parameterValues: {
      'token:clientId': sharepointClientId
      'token:clientSecret': keyVault.getSecret('sharepoint-client-secret')
      'token:TenantId': sharepointTenantId
      'token:grantType': 'client_credentials'
    }
  }
}
```

### Required Pre-Provisioned Resources
| Resource | Description | Owner |
|---|---|---|
| Entra ID app registration | App with `Sites.ReadWrite.All` application permission (admin-consented) | Infrastructure/security team |
| Key Vault secret `sharepoint-client-secret` | Client secret for the Entra ID app registration | Infrastructure/security team |
| SharePoint site + document library | Target site and library for file uploads | Business/SharePoint admin |

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Managed identity for SharePoint connector | Not supported in Logic App Consumption tier for application permissions |
| User-delegated OAuth (interactive) | Logic App runs unattended — no interactive sign-in possible |
| SharePoint app-only with certificate | More complex provisioning; client secret is simpler for Logic App connector |
| Graph API direct call from Logic App HTTP action | Requires manual token management; connector handles auth lifecycle |

---

## 10. Filename Metadata Parsing

### Decision
Parse document metadata from the SFTP filename using a configurable underscore delimiter within the Logic App workflow using native expression functions (`split()`, `substring()`).

### Rationale
- FR-017 specifies filename convention: `{Account}_{Fund}_{DocType}_{Name}_{PublishedDate}_{EffectiveDate}.{ext}`.
- Logic App expression functions (`split()`, `length()`, `substring()`) can handle this parsing without external code.
- The delimiter is configurable via a Logic App parameter (`filenameDelimiter`, default `_`), allowing future flexibility if the naming convention changes.
- Date fields (`YYYYMMDD`) are converted to ISO 8601 format using Logic App `formatDateTime()` or string concatenation (`@{substring(segment,0,4)}-@{substring(segment,4,2)}-@{substring(segment,6,2)}`).
- If parsing fails (wrong number of segments or invalid dates), the Cosmos DB record is created with `status: "error"` and `metadataParseError` set. No SharePoint upload or Service Bus message. File remains on SFTP.

### Parsing Logic (Logic App expressions)

```text
1. Strip extension: split(filename, '.')[0] → baseName
2. Split by delimiter: split(baseName, parameters('filenameDelimiter')) → segments[]
3. Validate: length(segments) == 6 → proceed; else → error
4. Extract:
   - segments[0] → account
   - segments[1] → fund
   - segments[2] → docType
   - segments[3] → docName
   - segments[4] → publishedDate (YYYYMMDD → ISO 8601)
   - segments[5] → effectiveDate (YYYYMMDD → ISO 8601)
```

### Alternatives Considered
| Alternative | Why Rejected |
|---|---|
| Regex-based parsing | More complex to maintain in Logic App expressions; split is simpler |
| Python-based parsing (in agent) | Only runs for PDFs; CSV/Excel never reach the agent |
| SFTP folder structure for metadata | User confirmed no folder structure — metadata is in filename only |
| Lookup table / external metadata API | Over-engineering; filename convention is the authoritative source |

---

## 11. Infrastructure Parameter Inventory

### Decision
Define the complete set of new Bicep parameters, Key Vault secrets, and Logic App parameters required for SFTP and SharePoint connectivity.

### Rationale
- All connection credentials and configuration values must be explicitly enumerated to prevent deployment failures. The second clarification round confirmed these were not yet created.
- Key Vault secrets are pre-provisioned by the infrastructure/security team. Bicep parameters are added to `dev.bicepparam` and `prod.bicepparam`. Logic App parameters are embedded in the workflow definition.

### Key Vault Secrets (pre-provisioned)
| Secret Name | Description |
|---|---|
| `sftp-private-key` | SSH private key (PEM format) for SFTP server authentication |
| `sharepoint-client-secret` | Client secret for SharePoint Entra ID app registration |

### Bicep Parameters (new, added to parameter files)
| Parameter | Type | Description | Example |
|---|---|---|---|
| `sftpHost` | `string` | SFTP server hostname | `sftp.partner.com` |
| `sftpPort` | `int` | SFTP server port | `22` |
| `sftpUsername` | `string` | SFTP login username | `intake-user` |
| `sftpFolderPath` | `string` | Monitored SFTP folder | `/inbox/` |
| `sftpArchiveFolderPath` | `string` | Archive folder for processed files | `/processed/` |
| `keyVaultName` | `string` | Key Vault name for secret retrieval | `kv-docproc-dev` |
| `sharepointClientId` | `string` | Entra ID app registration client ID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `sharepointTenantId` | `string` | Entra ID tenant ID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `sharepointSiteUrl` | `string` | SharePoint Online site URL | `https://contoso.sharepoint.com/sites/pe-docs` |
| `sharepointDocLibraryPath` | `string` | Root document library path | `Documents` |

### Logic App Parameters (embedded in workflow)
| Parameter | Type | Default | Description |
|---|---|---|---|
| `filenameDelimiter` | `string` | `_` | Delimiter for filename metadata parsing |
Example: `Documents/H/HorizonCapital/GrowthFundIII/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv`
