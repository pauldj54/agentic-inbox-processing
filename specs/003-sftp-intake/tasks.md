# Tasks: SFTP File Intake Channel

**Input**: Design documents from `/specs/003-sftp-intake/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Included — CAR-007 mandates test scenarios for CSV/Excel SharePoint upload, PDF classification routing, PDF triage-only routing, and unsupported file type handling.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add new Bicep parameters for SFTP, SharePoint, and Key Vault connectivity to environment parameter files

- [X] T001 Add SFTP parameters (`sftpHost`, `sftpPort`, `sftpUsername`, `sftpFolderPath`, `sftpArchiveFolderPath`), Key Vault parameter (`keyVaultName`), and SharePoint parameters (`sharepointClientId`, `sharepointTenantId`, `sharepointSiteUrl`, `sharepointDocLibraryPath`) to `infrastructure/parameters/dev.bicepparam`
- [X] T002 [P] Add the same SFTP, Key Vault, and SharePoint parameters to `infrastructure/parameters/prod.bicepparam`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Cosmos DB container rename (`emails` → `intake-records`), new Bicep module for SFTP Logic App and API Connections, code reference updates, and migration tooling

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 [P] Rename Cosmos DB container from `emails` to `intake-records` and add composite index on `intakeSource`+`status` in `infrastructure/modules/cosmos-db.bicep`
- [X] T004 [P] Create `infrastructure/modules/sftp-logic-app.bicep` with: Logic App Consumption resource (`sftp-file-ingestion`), `sftpwithssh` API Connection (`Microsoft.Web/connections`) with SSH private key from Key Vault via `getSecret('sftp-private-key')`, `sharepointonline` API Connection with Entra ID client credentials (`token:clientId`, `token:clientSecret` from Key Vault via `getSecret('sharepoint-client-secret')`, `token:TenantId`, `token:grantType=client_credentials`), `$connections` parameter linking existing `azureblob`/`documentdb`/`servicebus` connections plus new `sftpwithssh`/`sharepointonline` connections
- [X] T005 Update `infrastructure/main.bicep` to add `sftp-logic-app` module invocation, passing SFTP params, Key Vault name, SharePoint params, and references to existing API connections (azureblob, documentdb, servicebus)
- [X] T006 [P] Update `infrastructure/modules/role-assignments.bicep` to add SFTP Logic App managed identity roles (Storage Blob Data Contributor, Service Bus Data Sender, Cosmos DB account-level contributor) matching the pattern used for the email Logic App identity
- [X] T007 [P] Rename constant `CONTAINER_EMAILS = "emails"` to `CONTAINER_INTAKE_RECORDS = "intake-records"` and update all usages in `src/agents/tools/cosmos_tools.py`
- [X] T008 [P] Update Cosmos DB container reference from `"emails"` to `"intake-records"` in all `get_container_client()` calls in `src/webapp/main.py`
- [X] T009 [P] Update Cosmos DB `containerId` references from `emails` to `intake-records` in all Cosmos DB actions in `logic-apps/email-ingestion/workflow.json`
- [X] T010 [P] Update container name reference in `logic-apps/email-ingestion/parameters.dev.json` if it contains a Cosmos container parameter
- [X] T011 [P] Create one-time migration script that copies all documents from `emails` to `intake-records` container, backfilling `intakeSource: "email"` on each document, with idempotency check (skip if document already exists in target) in `utils/migrate_cosmos_container.py`
- [X] T012 [P] Update container references from `emails` to `intake-records` in `tests/integration/test_flow.py`
- [X] T013 [P] Update container references from `emails` to `intake-records` in `tests/integration/test_link_download_flow.py`

**Checkpoint**: Container renamed in Bicep and all code references. SFTP Logic App and API Connections provisioned in Bicep. Migration script ready. All existing tests pass with new container name.

---

## Phase 3: User Story 1 — Ingest Excel/CSV Files from SFTP (Priority: P1) 🎯 MVP

**Goal**: CSV/Excel files deposited in the SFTP folder are detected, downloaded via SSH-key-authenticated SFTP, metadata parsed from filename, backed up to Blob Storage, logged in Cosmos DB with parsed metadata, uploaded to SharePoint with structured folder path `{root}/{letter}/{Account}/{Fund}/{filename}`, and archived on SFTP.

**Independent Test**: Place a CSV file named `HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv` into the SFTP `/inbox/` folder. Verify: blob backup at `/attachments/sftp-{fileId}/{filename}`, Cosmos DB record with parsed metadata and `sharepointPath`, SharePoint file at `Documents/H/HorizonCapital/GrowthFundIII/{filename}`, no Service Bus message, file moved to `/processed/`.

### Implementation for User Story 1

- [X] T014 [US1] Create Logic App workflow definition with SFTP-SSH trigger ("When files are added or modified" on configurable folder path via `sftpwithssh` API Connection), "Get file content" action, and MD5 content hash Compose action in `logic-apps/sftp-file-ingestion/workflow.json`
- [X] T015 [US1] Add duplicate detection: Cosmos DB query action (`SELECT VALUE COUNT(1) FROM c WHERE c.sftpPath = @sftpPath AND c.contentHash = @contentHash AND c.intakeSource = 'sftp'`) with condition to skip processing if count > 0 (log warning, do NOT move file) in `logic-apps/sftp-file-ingestion/workflow.json`
- [X] T016 [US1] Add filename metadata parsing: split filename (without extension) by configurable `filenameDelimiter` parameter (default `_`), validate 6 segments, extract positional fields (Account, Fund, DocType, DocName, PublishedDate, EffectiveDate), convert YYYYMMDD dates to ISO 8601, and error handling branch for wrong segment count or invalid dates (create Cosmos DB record with `status: "error"` and `metadataParseError`, skip remaining steps, do NOT move file) in `logic-apps/sftp-file-ingestion/workflow.json`
- [X] T017 [US1] Add blob storage upload action (`/attachments/sftp-{fileId}/{filename}` via `azureblob` connection) and Cosmos DB "Create or update document" action (intake record with `intakeSource: "sftp"`, parsed metadata fields, `status: "received"`, blob path, file size, content hash, SFTP path) in `logic-apps/sftp-file-ingestion/workflow.json`
- [X] T018 [US1] Add file type routing Switch on extension: CSV/Excel branch constructs SharePoint folder path (`{sharepointDocLibraryPath}/{first letter of account}/{account}/{fund}/`) and uploads file via SharePoint "Create file" action using `sharepointonline` connection; PDF branch sends SFTP intake message (fileId, originalFilename, fileType, blobPath, intakeSource, receivedAt, sftpPath, contentHash, fileSize, parsed metadata) to `email-intake` Service Bus queue; default branch logs warning and skips for unsupported file types in `logic-apps/sftp-file-ingestion/workflow.json`
- [X] T019 [US1] Add Cosmos DB update action to set `sharepointPath` and `status: "archived"` (CSV/Excel) or `queue: "email-intake"` (PDF) after routing, then SFTP "Rename file" action to move file from monitored folder to `sftpArchivePath` for successful processing in `logic-apps/sftp-file-ingestion/workflow.json`
- [X] T020 [US1] Create Logic App dev parameters file with `sftpFolderPath`, `sftpArchivePath`, `cosmosDbAccountName`, `cosmosDbDatabaseName`, `serviceBusNamespace`, `sharepointSiteUrl`, `sharepointDocLibraryPath`, `filenameDelimiter`, and `$connections` referencing all five API connections (sftpwithssh, sharepointonline, azureblob, documentdb, servicebus) in `logic-apps/sftp-file-ingestion/parameters.dev.json`
- [X] T021 [P] [US1] Create integration test file with CSV/Excel SharePoint routing tests: (1) CSV file → blob backup + Cosmos record with parsed metadata + `sharepointPath` populated + `status: "archived"`, (2) Excel .xlsx file → same flow, (3) filename parse failure → Cosmos record with `status: "error"` and `metadataParseError` + no SharePoint upload in `tests/integration/test_sftp_intake_flow.py`

**Checkpoint**: CSV/Excel files are fully processed end-to-end (SFTP → Blob → Cosmos DB → SharePoint → archive). PDF files are routed to Service Bus for agent processing (Phase 4). Unsupported file types are logged and skipped.

---

## Phase 4: User Story 2 — Ingest PDF Files with Full Classification (Priority: P1)

**Goal**: PDF files from SFTP are sent to the `email-intake` queue by the Logic App (Phase 3), picked up by the existing Python agent, classified without email-specific metadata, and routed to `archival-pending`, `human-review`, or `discarded` based on confidence.

**Independent Test**: Place a PDF file in the SFTP folder with `PIPELINE_MODE=full`. Verify: blob backup exists, Cosmos DB record has classification results, message routed to correct output queue based on confidence threshold.

### Implementation for User Story 2

- [X] T022 [US2] Detect `intakeSource: "sftp"` in `process_next_email()` by checking `message_data.get("intakeSource")` and branch to parse SFTP-specific fields (`fileId`, `originalFilename`, `blobPath`, `fileType`) instead of email fields (`emailId`, `from`, `subject`); use `fileId` as the document `id` when fetching/updating the Cosmos DB record in `src/agents/email_classifier_agent.py`
- [X] T023 [US2] Skip link download step (Step 1.5) for SFTP-sourced records by wrapping the link download logic in an `if intake_source != "sftp"` guard in `src/agents/email_classifier_agent.py`
- [X] T024 [P] [US2] Adapt relevance check and classification prompts for SFTP PDFs: omit email-specific context (sender, subject, body), use `originalFilename` and `fileType` as source context with `Source: SFTP file intake` header, analyze attachment content only via `blobPath` in `src/agents/classification_prompts.py`
- [X] T025 [P] [US2] Add PDF classification routing tests to `tests/integration/test_sftp_intake_flow.py`: (1) PDF in full mode → `email-intake` queue → agent classifies → routed to `archival-pending` (confidence ≥ 65%) or `human-review` (confidence < 65%) or `discarded`, (2) SFTP PDF record has no email-specific fields in classification context

**Checkpoint**: PDF files from SFTP are classified by the agent using attachment content only (no email metadata). Classification routing (archival-pending / human-review / discarded) works identically to email PDFs.

---

## Phase 5: User Story 3 — Ingest PDF Files in Triage-Only Mode (Priority: P2)

**Goal**: When `PIPELINE_MODE=triage-only`, SFTP-sourced PDFs bypass classification and route directly to the `triage-complete` queue for external IDP processing.

**Independent Test**: Set `PIPELINE_MODE=triage-only`, place a PDF in SFTP. Verify Cosmos DB record has `pipelineMode: "triage-only"`, `stepsExecuted` does not include `"classification"`, and message routed to `triage-complete` queue.

### Implementation for User Story 3

- [X] T026 [US3] Verify SFTP-sourced PDFs respect `PIPELINE_MODE=triage-only` routing: ensure the existing triage-only path in `process_next_email()` works for records with `intakeSource: "sftp"`, setting `pipelineMode: "triage-only"` and routing to `triage-complete` queue without executing classification steps in `src/agents/email_classifier_agent.py`
- [X] T027 [P] [US3] Add triage-only routing test to `tests/integration/test_sftp_intake_flow.py`: SFTP PDF in triage-only mode → routed to `triage-complete` queue, Cosmos DB record shows `pipelineMode: "triage-only"`, no classification step executed

**Checkpoint**: SFTP PDF processing correctly respects pipeline mode configuration, matching existing email pipeline behavior.

---

## Phase 6: User Story 4 — SFTP File Visibility on Dashboard (Priority: P3)

**Goal**: The existing dashboard displays SFTP-sourced records alongside email records with a visual source indicator, filename display for SFTP records, and SharePoint path for CSV/Excel SFTP records.

**Independent Test**: Process files via SFTP, navigate to dashboard. Verify SFTP records appear with "SFTP" badge in Source column, `originalFilename` shown instead of email `subject`, and `sharepointPath` displayed for CSV/Excel records.

### Implementation for User Story 4

- [X] T028 [US4] Update dashboard Cosmos DB query to include `intakeSource` field in results and default to `"email"` for legacy records missing `intakeSource` in `src/webapp/main.py`
- [X] T029 [US4] Add "Source" column to the dashboard table with "Email" (blue badge) / "SFTP" (green badge) based on `intakeSource` field, defaulting to "Email" for records without `intakeSource` in `src/webapp/templates/dashboard.html`
- [X] T030 [US4] Display `originalFilename` for SFTP records instead of email `subject`, show `fileType` badge, and show `sharepointPath` for CSV/Excel SFTP records in the detail section in `src/webapp/templates/dashboard.html`

**Checkpoint**: Dashboard provides full visibility into both intake channels. Legacy email records display correctly with backward compatibility.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Regression testing, validation, and documentation

- [X] T031 [P] Create unit test verifying `CONTAINER_INTAKE_RECORDS` constant equals `"intake-records"` and old `CONTAINER_EMAILS` constant no longer exists in `tests/unit/test_container_rename.py`
- [X] T032 [P] Add unsupported file type skip test to `tests/integration/test_sftp_intake_flow.py`: `.docx`/`.txt` file → logged as unsupported, not processed, not moved from SFTP folder
- [X] T033 [P] Run existing unit and integration tests to verify container rename and agent changes cause no regressions: `pytest tests/ -v`
- [X] T034 [P] Create `logic-apps/sftp-file-ingestion/README.md` with workflow documentation: trigger configuration, 11-step workflow actions, file type routing rules, SharePoint folder path convention, filename metadata parsing logic, error handling table, and Logic App parameter descriptions
- [X] T035 Run `quickstart.md` validation scenarios end-to-end: unit tests (scenario 1), CSV/Excel SharePoint upload (scenario 3), filename parse failure (scenario 3.5), PDF full classification (scenario 4), unsupported file type (scenario 7), dashboard visibility (scenario 8), migration script (scenario 9)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Can start in parallel with Phase 1. **BLOCKS all user stories**.
- **User Story 1 (Phase 3)**: Depends on Phase 1 (parameter files) + Phase 2 (Bicep module, container rename)
- **User Story 2 (Phase 4)**: Depends on Phase 3 (Logic App workflow sends PDFs to `email-intake` Service Bus queue)
- **User Story 3 (Phase 5)**: Depends on Phase 4 (agent SFTP source detection must exist)
- **User Story 4 (Phase 6)**: Depends on Phase 2 (container rename). Can start in parallel with Phases 3–5.
- **Polish (Phase 7)**: Depends on all previous phases

### User Story Dependencies

```
Phase 1 (Setup) ──┐
                   ├──► Phase 3 (US1: CSV/Excel → SharePoint) ──► Phase 4 (US2: PDF Classification) ──► Phase 5 (US3: Triage-Only)
Phase 2 (Found.) ─┤
                   └──► Phase 6 (US4: Dashboard) ─────────────────────────────────────────────────────────────────────────────────┐
                                                                                                                                   ├──► Phase 7 (Polish)
Phase 3 + Phase 4 + Phase 5 ──────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Within Each User Story

- Bicep modules before Logic App workflow definitions
- Logic App workflow steps are sequential (each action builds on previous outputs)
- Agent changes: source detection → skip guard → prompt adaptation
- Dashboard: backend query → HTML column → detail display

### Parallel Opportunities

**Phase 1**: T001 and T002 run in parallel (different parameter files).

**Phase 2**: T003, T004, T006–T013 can ALL run in parallel (each edits a different file). T005 depends on T004 (main.bicep references the new sftp-logic-app module).

**Phase 3**: T014 creates the workflow file; T015–T019 are sequential edits to the same `workflow.json`. T020 (parameters.dev.json) can run in parallel with T014. T021 (tests) can run in parallel with workflow tasks (different file).

**Phase 4**: T024 (prompts) and T025 (tests) can run in parallel with T022–T023 (agent code) since they edit different files.

**Phase 6**: T028 (main.py) and T029 (dashboard.html) can run in parallel. T030 depends on T029 (same file).

**Phase 7**: T031, T032, T033, T034 can all run in parallel.

---

## Parallel Example: User Story 1

```bash
# After Phase 2 checkpoint, launch in parallel:
Task T014: "Create Logic App workflow with SFTP-SSH trigger in workflow.json"
Task T020: "Create parameters.dev.json for SFTP Logic App"
Task T021: "Create CSV/Excel routing integration tests in test_sftp_intake_flow.py"

# Then sequentially within workflow.json:
Task T015: "Add duplicate detection action"
Task T016: "Add filename metadata parsing"
Task T017: "Add blob upload and Cosmos DB upsert"
Task T018: "Add file type routing Switch (SharePoint / Service Bus / skip)"
Task T019: "Add Cosmos update and SFTP archive move"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (parameter files)
2. Complete Phase 2: Foundational (Bicep module, container rename, migration script)
3. Complete Phase 3: User Story 1 (SFTP Logic App workflow, CSV/Excel → SharePoint)
4. **STOP and VALIDATE**: Test CSV/Excel files end-to-end with quickstart scenario 3
5. Deploy/demo if ready — PDFs are queued but agent doesn't consume them yet

### Incremental Delivery

1. **Increment 1** (Phases 1–3): SFTP intake works for CSV/Excel with SharePoint archival. PDFs are queued but not yet consumed by agent.
2. **Increment 2** (Phase 4): Agent processes SFTP PDFs with full classification. End-to-end PDF flow works.
3. **Increment 3** (Phase 5): Triage-only mode verified for SFTP PDFs. External IDP handoff works.
4. **Increment 4** (Phase 6): Dashboard shows SFTP records with source indicators and SFTP-specific detail fields.
5. **Increment 5** (Phase 7): Full regression testing, quickstart validation, and documentation.

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1 (Logic App workflow)
   - Developer B: User Story 4 (Dashboard — no dependency on US1)
3. After US1 complete:
   - Developer A: User Story 2 (Agent SFTP detection)
4. After US2 complete:
   - Developer A: User Story 3 (Triage-only verification)
5. Polish phase after all stories complete

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Auth: SFTP uses SSH private key (not certificate — Key Vault `sftp-private-key`); SharePoint uses Entra ID app client credentials (not managed identity — Key Vault `sharepoint-client-secret`). See research.md §2 and §9.
- Key Vault secrets (`sftp-private-key`, `sharepoint-client-secret`) are pre-provisioned by infra/security team before Bicep deployment
