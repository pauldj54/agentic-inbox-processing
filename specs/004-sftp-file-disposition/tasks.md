# Tasks: SFTP File Disposition (Success/Failure Routing)

**Input**: Design documents from `/specs/004-sftp-file-disposition/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Not explicitly requested. Manual validation steps are included at checkpoints per quickstart.md.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Infrastructure prerequisites and parameter configuration

- [x] T001 Create `/failed/` folder on SFTP server at `doc-exchange/failed/` in storage account `sftpprocdevizr2ch55`
- [x] T002 Add `sftpFailedPath` parameter (type String, default `"/failed/"`) to the `definition.parameters` section in logic-apps/sftp-file-ingestion/workflow.json
- [x] T003 [P] Add `sftpFailedPath` parameter value to logic-apps/sftp-file-ingestion/parameters.dev.json

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Restructure the workflow with two Scopes to support disposition branching for ALL failure types. MUST be complete before any user story implementation.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T004 Wrap `Get_file_content`, `Extract_metadata`, `Upload_to_blob`, and `Compute_dedup_key` inside a new `Scope_Early_Processing` scope action in logic-apps/sftp-file-ingestion/workflow.json. The scope's `runAfter` should reference the existing trigger output chain (same as current `Get_file_content` runAfter).
- [x] T005 Wrap `Create_intake_record_if_new` and `Check_if_spreadsheet` (with all nested actions including `Upload_to_SharePoint`, `Check_if_PDF`, `Compose_Service_Bus_Message`, `Send_to_Service_Bus`, `Log_unsupported_type`) inside a new `Scope_Route_File` scope action in logic-apps/sftp-file-ingestion/workflow.json
- [x] T006 Update `runAfter` chain: `Scope_Early_Processing` [Succeeded] → `Check_for_duplicate` → `Handle_duplicate_check` → `Scope_Route_File` [Succeeded from Handle_duplicate_check]. Adjust internal action `runAfter` references so `Create_intake_record_if_new` runs as the first-in-scope action in logic-apps/sftp-file-ingestion/workflow.json
- [x] T007 Remove `Delete_file` action and its `runAfter` reference from logic-apps/sftp-file-ingestion/workflow.json (replaced by disposition paths in Phase 3 and 4)
- [x] T008 Remove `Terminate_success` action from its current top-level location in logic-apps/sftp-file-ingestion/workflow.json (will be relocated inside `Check_if_supported_type` true branch in Phase 3)

**Checkpoint**: Workflow has `Scope_Early_Processing` wrapping early processing and `Scope_Route_File` wrapping downstream processing, with `Handle_duplicate_check` sitting between them. No disposition actions yet — files are processed but not moved or deleted from `/in/`.

---

## Phase 3: User Story 1 — Move Successfully Processed Files to /processed (Priority: P1) 🎯 MVP

**Goal**: On successful processing, move the file from `/in/` to `/processed/` and update Cosmos DB with `disposition: "processed"`. True duplicates are also moved to `/processed/`. Unsupported file types are deleted from `/in/` (matching existing behavior).

**Independent Test**: Place a valid CSV in SFTP `/in/`. Wait for Logic App trigger. Verify file is in `/processed/`, absent from `/in/`, and Cosmos record has `disposition: "processed"`.

### Implementation for User Story 1

- [x] T009 [US1] Add `Check_if_supported_type` If condition (expression: `contains(createArray('csv','xlsx','xls','pdf'), outputs('Parse_file_extension'))`) with runAfter `Scope_Route_File` [Succeeded] in logic-apps/sftp-file-ingestion/workflow.json
- [x] T010 [US1] Add `Copy_to_processed` SFTP copy action in `Check_if_supported_type` true branch (source: `triggerOutputs()?['headers']['x-ms-file-path']`, dest: `concat(parameters('sftpArchivePath'), triggerOutputs()?['headers']['x-ms-file-name'])`, overwrite: true, connection: `sftpwithssh-1`) in logic-apps/sftp-file-ingestion/workflow.json
- [x] T011 [US1] Add `Update_Cosmos_processed` Cosmos DB upsert action (set `disposition: "processed"`, partition key: `{sftpUsername}_{YYYY-MM}`, connection: `documentdb`, header: `x-ms-documentdb-is-upsert: true`) after `Copy_to_processed` [Succeeded] in logic-apps/sftp-file-ingestion/workflow.json
- [x] T012 [US1] Add `Delete_from_in` SFTP delete action (file ID: `triggerOutputs()['headers']['x-ms-file-id']`, connection: `sftpwithssh-1`) after `Update_Cosmos_processed` [Succeeded] in logic-apps/sftp-file-ingestion/workflow.json
- [x] T013 [US1] Add `Terminate_success` terminate action (status: Succeeded) after `Delete_from_in` [Succeeded] inside `Check_if_supported_type` true branch in logic-apps/sftp-file-ingestion/workflow.json
- [x] T014 [US1] Add `Delete_unsupported_from_in` SFTP delete action (file ID: `triggerOutputs()['headers']['x-ms-file-id']`, connection: `sftpwithssh-1`) inside `Check_if_supported_type` false branch in logic-apps/sftp-file-ingestion/workflow.json — matches existing delete behavior for unsupported files
- [x] T015 [US1] Add `Terminate_skipped` terminate action (status: Succeeded) after `Delete_unsupported_from_in` [Succeeded] inside `Check_if_supported_type` false branch in logic-apps/sftp-file-ingestion/workflow.json
- [x] T016 [US1] Add `Copy_dup_to_processed` SFTP copy action (source: trigger file path, dest: `sftpArchivePath` + filename, overwrite: true) after `Patch_delivery_count` [Succeeded] inside `Handle_duplicate_check` → `Compare_content_hash` → same-hash branch in logic-apps/sftp-file-ingestion/workflow.json
- [x] T017 [US1] Add `Delete_dup_from_in` SFTP delete action (using trigger file ID) after `Copy_dup_to_processed` [Succeeded] and update `Terminate_duplicate` runAfter to `Delete_dup_from_in` [Succeeded] in logic-apps/sftp-file-ingestion/workflow.json

**Checkpoint**: Successfully processed files and true duplicates are moved to `/processed/`. Unsupported types are deleted from `/in/`. Cosmos DB records show `disposition: "processed"`. Failures are unhandled (file stays in `/in/`).

---

## Phase 4: User Story 2 — Move Failed Files to /failed (Priority: P1)

**Goal**: On processing failure (inside `Scope_Route_File` or `Scope_Early_Processing`), move the file from `/in/` to `/failed/` and update Cosmos DB where possible. Early failures (before `Compute_dedup_key`) have no Cosmos DB update — the file in `/failed/` is the sole indicator. Unexpected dedup errors also route to `/failed/`.

**Independent Test**: Simulate a SharePoint upload failure (e.g., invalid credentials). Place a CSV in SFTP `/in/`. Verify file is in `/failed/`, absent from `/in/`, and Cosmos record has `disposition: "failed"` with `errorDetails`. For early failure: temporarily break blob storage connectivity, place a file in `/in/`, verify it lands in `/failed/` with no Cosmos record.

### Implementation for User Story 2

#### Downstream failure path (Scope_Route_File failures)

- [x] T018 [US2] Add `Copy_to_failed` SFTP copy action (source: `triggerOutputs()?['headers']['x-ms-file-path']`, dest: `concat(parameters('sftpFailedPath'), triggerOutputs()?['headers']['x-ms-file-name'])`, overwrite: true, connection: `sftpwithssh-1`) with runAfter `Scope_Route_File` [Failed, TimedOut] in logic-apps/sftp-file-ingestion/workflow.json
- [x] T019 [US2] Add `Update_Cosmos_failed` Cosmos DB upsert action (set `status: "error"`, `disposition: "failed"`, `errorDetails: { actionName, errorMessage }` extracted from `result('Scope_Route_File')`, header: `x-ms-documentdb-is-upsert: true`) after `Copy_to_failed` [Succeeded] in logic-apps/sftp-file-ingestion/workflow.json
- [x] T020 [US2] Add `Delete_from_in_on_failure` SFTP delete action (file ID: `triggerOutputs()['headers']['x-ms-file-id']`, connection: `sftpwithssh-1`) after `Update_Cosmos_failed` [Succeeded] in logic-apps/sftp-file-ingestion/workflow.json
- [x] T021 [US2] Add `Terminate_failed` terminate action (status: Failed, error code: `"ProcessingFailed"`, error message from failed action) after `Delete_from_in_on_failure` [Succeeded] in logic-apps/sftp-file-ingestion/workflow.json

#### Dedup error path (unexpected errors in Handle_duplicate_check)

- [x] T022 [US2] Add `Copy_err_to_failed` SFTP copy action (source: trigger file path, dest: `sftpFailedPath` + filename, overwrite: true) in `Handle_duplicate_check` → `Check_if_new_file` → non-404 branch (before `Terminate_unexpected_error`) in logic-apps/sftp-file-ingestion/workflow.json
- [x] T023 [US2] Add `Delete_err_from_in` SFTP delete action (using trigger file ID) after `Copy_err_to_failed` [Succeeded] and update `Terminate_unexpected_error` runAfter to `Delete_err_from_in` [Succeeded] in logic-apps/sftp-file-ingestion/workflow.json

#### Early failure path (Scope_Early_Processing failures)

- [x] T024 [US2] Add `Copy_early_to_failed` SFTP copy action (source: `triggerOutputs()?['headers']['x-ms-file-path']`, dest: `concat(parameters('sftpFailedPath'), triggerOutputs()?['headers']['x-ms-file-name'])`, overwrite: true, connection: `sftpwithssh-1`) with runAfter `Scope_Early_Processing` [Failed, TimedOut] in logic-apps/sftp-file-ingestion/workflow.json
- [x] T025 [US2] Add `Delete_early_from_in` SFTP delete action (file ID: `triggerOutputs()['headers']['x-ms-file-id']`, connection: `sftpwithssh-1`) after `Copy_early_to_failed` [Succeeded] in logic-apps/sftp-file-ingestion/workflow.json
- [x] T026 [US2] Add `Terminate_early_failed` terminate action (status: Failed, error code: `"EarlyProcessingFailed"`, error message from `result('Scope_Early_Processing')`) after `Delete_early_from_in` [Succeeded] in logic-apps/sftp-file-ingestion/workflow.json — NO Cosmos DB update (metadata not yet computed; file in `/failed/` is sole indicator)

**Checkpoint**: All 5 disposition paths are functional — success → `/processed/`, downstream failure → `/failed/`, duplicate → `/processed/`, dedup-error → `/failed/`, early failure → `/failed/`. Cosmos DB records reflect all outcomes where metadata was computed.

---

## Phase 5: User Story 3 — Report File Outcomes to Data Providers (Priority: P2)

**Goal**: Operators can determine file outcomes by inspecting SFTP folders and querying Cosmos DB `disposition` field, enabling reporting to data providers.

**Independent Test**: Process a batch of files (mix of valid, invalid, duplicate, unsupported). Verify `/processed/` and `/failed/` contain the correct files, `/in/` is empty, and Cosmos DB `disposition` values match folder contents.

### Implementation for User Story 3

No additional code changes required — US3 is delivered by the combined outcomes of US1 and US2. The SFTP folder structure and Cosmos DB `disposition` field enable reporting.

- [x] T027 [US3] Run end-to-end batch validation: process a mixed batch of files (success CSV + failure-inducing file + duplicate re-upload + unsupported .docx + early-failure file) and verify all 5 disposition paths produce correct SFTP folder contents and Cosmos DB records per specs/004-sftp-file-disposition/quickstart.md

**Checkpoint**: All user stories are functional. Operators can report file outcomes to data providers by folder inspection and Cosmos DB queries.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation and deployment validation

- [x] T028 [P] Update logic-apps/sftp-file-ingestion/README.md — add `Scope_Early_Processing` and `Scope_Route_File` to action table, document `/failed/` folder, add error handling section, update the workflow step descriptions to reflect disposition paths
- [x] T029 Deploy updated workflow.json to Logic App using REST API PUT pattern per deploy_updates.ps1 and verify all 5 disposition paths in Azure portal run history
- [x] T030 Run full quickstart.md validation (all 5 test scenarios: success, downstream failure, duplicate, unsupported, early failure) per specs/004-sftp-file-disposition/quickstart.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on T002 (parameter must exist) — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational phase completion (T004–T008)
- **User Story 2 (Phase 4)**: Depends on Foundational phase completion — can proceed in parallel with US1 (different `runAfter` paths)
- **User Story 3 (Phase 5)**: Depends on US1 and US2 completion (reporting requires all disposition paths)
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) — no dependency on other stories
- **User Story 2 (P1)**: Can start after Foundational (Phase 2) — no dependency on US1 (adds actions to different `runAfter` branches: `[Failed, TimedOut]` vs `[Succeeded]`)
- **User Story 3 (P2)**: Depends on US1 + US2 — end-to-end validation of combined outcome

### Within Each User Story

- Actions are added sequentially within the same workflow path (each depends on the prior action's `runAfter`)
- Copy actions run first, then Cosmos DB update (if applicable), then SFTP delete, then Terminate
- Duplicate/error/early disposition actions (T016–T017, T022–T023, T024–T026) are independent of the main disposition chains (T009–T015, T018–T021) since they modify different sections of the workflow

### Parallel Opportunities

- T002 and T003 can run in parallel (different files: workflow.json vs parameters.dev.json)
- T001 (SFTP folder creation) is independent of all code tasks
- US1 (Phase 3) and US2 (Phase 4) modify different `runAfter` branches and can be implemented in parallel
- Within US1: T009–T015 (main success path) and T016–T017 (duplicate path) modify different action groups and can be parallelized
- Within US2: T018–T021 (downstream failure), T022–T023 (dedup-error), and T024–T026 (early failure) modify different sections and can be parallelized
- T028 (README) can run in parallel with T029 (deployment)

---

## Parallel Example: User Story 1 + User Story 2

```
# After Foundational phase is complete, both stories can start simultaneously:

# US1 adds actions after Scope_Route_File [Succeeded]:
#   Check_if_supported_type → Copy_to_processed → Update_Cosmos_processed → Delete_from_in → Terminate_success
#   (plus Delete_unsupported_from_in → Terminate_skipped in false branch)
#   (plus Copy_dup_to_processed → Delete_dup_from_in in Handle_duplicate_check same-hash branch)

# US2 adds actions on three failure paths:
#   Scope_Route_File [Failed, TimedOut] → Copy_to_failed → Update_Cosmos_failed → Delete_from_in_on_failure → Terminate_failed
#   Handle_duplicate_check non-404 → Copy_err_to_failed → Delete_err_from_in → Terminate_unexpected_error
#   Scope_Early_Processing [Failed, TimedOut] → Copy_early_to_failed → Delete_early_from_in → Terminate_early_failed

# These are independent runAfter paths — no conflict in workflow.json.
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (create `/failed/` folder, add `sftpFailedPath` parameter)
2. Complete Phase 2: Foundational (`Scope_Early_Processing`, `Scope_Route_File`, remove `Delete_file`)
3. Complete Phase 3: User Story 1 (success disposition + duplicate disposition + unsupported delete)
4. **STOP and VALIDATE**: Test success, duplicate, and unsupported paths independently
5. Deploy and verify in Azure portal run history

### Incremental Delivery

1. Setup + Foundational → Workflow restructured with two Scopes, ready for disposition logic
2. Add User Story 1 → Test success + duplicate + unsupported paths → Deploy (MVP!)
3. Add User Story 2 → Test downstream failure + dedup-error + early failure paths → Deploy
4. Add User Story 3 → End-to-end batch validation → Deploy
5. Polish → README update, full quickstart validation

---

## Notes

- All workflow changes are in a single file: `logic-apps/sftp-file-ingestion/workflow.json`
- Copy actions use `x-ms-file-path` (literal path). Delete actions use `x-ms-file-id` (encoded ID). This is critical for UTF-8 special character handling per FR-007 and FR-008.
- The `sftpwithssh-1` connection is used for all SFTP copy/delete operations (consistent with existing patterns).
- The `documentdb` connection with managed identity is used for all Cosmos DB upsert operations.
- Cosmos DB upserts require `x-ms-documentdb-is-upsert: true` header.
- The SFTP trigger (`onupdatedfile`) uses a watermark model — it advances past each file regardless of run success or failure. Files that fail and stay in `/in/` are NEVER automatically re-triggered (per FR-011 and research.md Decision 1). This is why ALL failures must route to `/failed/` via the two-Scope architecture.
- Early failures (before `Compute_dedup_key`) have no Cosmos DB update because the document ID and partition key have not been computed yet. The file in `/failed/` is the sole indicator of these failures. Recovery is the same: copy from `/failed/` back to `/in/`.
- The Cosmos DB duplicate-check returning HTTP 404 is a managed/expected condition (green path for new files). It is NOT a failure and does NOT trigger `/failed/` disposition.
