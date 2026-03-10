# Implementation Plan: SFTP File Intake Channel

**Branch**: `003-sftp-intake` | **Date**: 2026-03-09 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-sftp-intake/spec.md`

## Summary

Add a second intake channel triggered by new files arriving in a monitored SFTP folder. A new Logic App Consumption workflow (`sftp-file-ingestion`) polls the SFTP server via the built-in SFTP-SSH managed connector, authenticated by SSH private key stored in Azure Key Vault. Files are routed by extension: CSV/Excel files have their filename metadata parsed, are backed up to blob storage, logged in Cosmos DB, and uploaded directly to SharePoint via the Logic App SharePoint connector (Entra ID app registration with `Sites.ReadWrite.All`). PDF files follow the same initial path (download → blob → Cosmos DB) but are routed to the `email-intake` Service Bus queue for classification by the existing Python agent. The Cosmos DB container is renamed from `emails` to `intake-records` with a unified schema using an `intakeSource` discriminator. Two new `Microsoft.Web/connections` API Connection resources are provisioned via Bicep: `sftpWithSsh` (SSH private key from Key Vault) and `sharepointonline` (Entra ID client secret from Key Vault).

## Technical Context

**Language/Version**: Python 3.12+ (agent, dashboard), Bicep (infrastructure), JSON (Logic App workflow)  
**Primary Dependencies**: FastAPI, azure-cosmos, azure-servicebus, azure-identity, azure-storage-blob (all existing — no new packages)  
**Storage**: Azure Cosmos DB (serverless, `email-processing` database, `intake-records` container), Azure Blob Storage, SharePoint Online  
**Testing**: pytest with `asyncio_mode="auto"`  
**Target Platform**: Azure (Logic Apps Consumption, App Service Linux, Service Bus Standard)  
**Project Type**: Event-driven document processing pipeline (web-service + serverless workflows)  
**Performance Goals**: CSV/Excel processed within 2 minutes of SFTP detection (SC-001). PDF classification within 5 minutes (SC-002).  
**Constraints**: SFTP polling interval ~1 minute. File size limit 50 MB. SharePoint connector requires Entra ID app registration (not managed identity).  
**Scale/Scope**: Low-volume batch processing (~tens of files/day). Single SFTP source, single SharePoint destination.

## Constitution Check (Pre-Design)

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Code simplicity gate**: ✅ PASS. SFTP intake is a separate Logic App workflow feeding into the existing pipeline. No classification logic is duplicated — PDFs reuse the existing agent. CSV/Excel are handled entirely within the Logic App (no new Python code for their lifecycle). The only Python changes are: (1) container rename constant, (2) agent source detection for SFTP messages, (3) dashboard source column.
- **UX gate**: ✅ PASS. Dashboard change is minimal — one "Source" column/badge (CAR-002). No new screens or flows.
- **Responsive gate**: ✅ PASS. The source column is a small badge that collapses naturally at mobile widths.
- **Dependency gate**: ✅ PASS. No new Python packages. SFTP-SSH and SharePoint connectors are built-in Logic App managed connectors. All Azure SDK packages already in `requirements.txt`.
- **Auth gate**: ⚠️ EXCEPTION DOCUMENTED. Two non-Entra-ID auth mechanisms required:
  - **SFTP-SSH**: SSH private key (Certificate/key auth is the only authentication method supported by the SFTP-SSH connector for remote SFTP servers. Entra ID is not applicable for external SFTP endpoints).
  - **SharePoint**: Entra ID app registration with client secret (application permissions `Sites.ReadWrite.All` for unattended Logic App file uploads. Managed identity is not supported by the Logic App SharePoint connector in Consumption tier).
  - Both secrets are stored in Azure Key Vault and injected at Bicep deployment time via `getSecret()`. Compensating controls: Key Vault access policies, secret rotation policy.
- **Validation gate**: ✅ PASS. Test plan is focused: unit tests for container rename, integration tests for SFTP intake flow (CSV/Excel SharePoint routing, PDF classification routing, duplicate detection), manual tests for end-to-end verification.
- **Logging gate**: ✅ PASS. All processing events use Python `logging` module with structured entries. Logic App run history provides built-in structured logging.

### Post-Design Re-Evaluation

All gates confirmed post-Phase 1 design. No new violations introduced:
- `data-model.md`: Clean schema extension with conditional fields — no complexity added.
- `contracts.md`: API Connection resources use Key Vault `getSecret()` at deploy time — auth exception boundaries unchanged.
- `quickstart.md`: 9+ test scenarios cover all happy paths and error cases — validation remains pragmatic.
- **No gate changes required.**

## Project Structure

### Documentation (this feature)

```text
specs/003-sftp-intake/
├── plan.md              # This file
├── research.md          # Phase 0 output — technology decisions & rationale
├── data-model.md        # Phase 1 output — Cosmos DB schema changes
├── quickstart.md        # Phase 1 output — manual testing guide
├── contracts/
│   └── contracts.md     # Phase 1 output — Service Bus, Cosmos DB, Logic App contracts
├── checklists/
│   └── requirements.md  # Requirements coverage checklist
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
infrastructure/
├── main.bicep                          # Add SFTP Logic App + API Connections modules
├── modules/
│   ├── cosmos-db.bicep                 # Rename container: emails → intake-records
│   ├── logic-app.bicep                 # Existing email Logic App (unchanged)
│   ├── sftp-logic-app.bicep            # NEW: SFTP file ingestion Logic App + API connections
│   ├── role-assignments.bicep          # Add SFTP Logic App identity roles
│   └── ...                             # Other modules unchanged
├── parameters/
│   ├── dev.bicepparam                  # Add SFTP/SharePoint/Key Vault params
│   └── prod.bicepparam                 # Add SFTP/SharePoint/Key Vault params

logic-apps/
├── email-ingestion/                    # Existing (update Cosmos container ref)
│   ├── workflow.json                   # Update container path: emails → intake-records
│   └── parameters.dev.json            # Update container name reference
└── sftp-file-ingestion/                # NEW: SFTP Logic App workflow
    ├── workflow.json                   # 11-step SFTP intake workflow
    └── parameters.dev.json            # SFTP/SharePoint connection params

src/
├── agents/
│   ├── tools/
│   │   └── cosmos_tools.py            # Rename CONTAINER_EMAILS → CONTAINER_INTAKE_RECORDS
│   └── email_classifier_agent.py      # Add intakeSource detection for SFTP
├── webapp/
│   ├── main.py                        # Update container ref, add source column data
│   └── templates/
│       └── dashboard.html             # Add Source column/badge

tests/
├── unit/
│   └── test_container_rename.py       # NEW: Verify constant change
└── integration/
    ├── test_flow.py                   # Update container references
    ├── test_link_download_flow.py     # Update container references
    └── test_sftp_intake_flow.py       # NEW: SFTP intake integration tests

utils/
└── migrate_cosmos_container.py        # NEW: One-time migration script (emails → intake-records)
```

**Structure Decision**: Follows existing single-project layout. SFTP Logic App gets its own Bicep module (`sftp-logic-app.bicep`) and workflow directory (`logic-apps/sftp-file-ingestion/`), mirroring the pattern established by the email Logic App. No new Python packages or project structure changes.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| SFTP-SSH: SSH private key auth (non-Entra) | External SFTP server — Entra ID not supported | Only option for SFTP-SSH connector authentication against external endpoints |
| SharePoint: Entra ID app registration with client secret (non-managed-identity) | Logic App Consumption tier SharePoint connector does not support managed identity for application permissions | User-delegated auth requires interactive sign-in — unusable for background Logic App |
