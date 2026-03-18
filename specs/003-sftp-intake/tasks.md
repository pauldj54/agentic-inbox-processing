# Tasks: SFTP File Intake — Content Hash Dedup & Delivery Tracking

**Input**: Design documents from `/specs/003-sftp-intake/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md §6 (dedup redesign), data-model.md (partition key change + new fields), contracts/ (workflow action order)

**Context**: The base SFTP intake workflow (14 actions) is deployed and working. These tasks implement the **content hash dedup, file update detection, and delivery tracking** enhancements described in plan.md.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US4)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Cosmos DB Partition Key Migration)

**Purpose**: Recreate the Cosmos DB container with partition key `/partitionKey` (replacing `/status`) and migrate existing data

- [X] T001 Update partition key from `/status` to `/partitionKey` in Cosmos DB container definition in `infrastructure/modules/cosmos-db.bicep`
- [X] T002 [P] Create the migration script `utils/migrate_container.py` to handle partition key change: create new `intake-records` container with `/partitionKey` partition key, copy all documents from old container setting `intakeSource` to `"email"` and computing `partitionKey` (= `{sender_domain}_{YYYY-MM}` from `from` field + `receivedAt`) for legacy records, verify document count matches, then delete old container. Script must be idempotent.
- [X] T003 [P] Run the migration script against the dev Cosmos DB instance (`cosmos-docproc-dev-izr2ch55woa3c`, database `email-processing`) to recreate the `intake-records` container with `/partitionKey` partition key and migrate existing documents

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Update all code that reads/writes Cosmos DB to use `/partitionKey` as partition key and `base64(sftpPath)` as document ID for SFTP records

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T004 Update all Cosmos DB point-read and query operations in `src/agents/tools/cosmos_tools.py` to use `partitionKey` as partition key value instead of `status` (e.g., `partition_key="{sender_domain}_{YYYY-MM}"` for email or `partition_key="{sftp_username}_{YYYY-MM}"` for SFTP)
- [X] T005 [P] Update all Cosmos DB operations in `src/webapp/main.py` (dashboard queries) to use cross-partition queries or specify `partitionKey` as partition key — dashboard must support cross-partition queries since partition values vary by source and month
- [X] T006 [P] Update Cosmos DB actions in the email ingestion Logic App `logic-apps/email-ingestion/workflow.json` to use computed `partitionKey` value (= `{sender_domain}_{YYYY-MM}`) instead of the document's `status` value in all Cosmos DB read/write actions
- [X] T007 [P] Update existing unit tests in `tests/unit/` to reflect partition key change from `status` to `partitionKey` in any Cosmos DB mocks or assertions

**Checkpoint**: All Cosmos DB operations across the codebase use `/partitionKey` as partition key. Existing tests pass.

---

## Phase 3: User Story 1 — Content Hash Dedup & Delivery Tracking for CSV/Excel (Priority: P1) 🎯 MVP

**Goal**: Reorder the SFTP workflow to upload blob BEFORE dedup check, use `base64(sftpPath)` as Cosmos doc ID, extract `ContentMD5` from blob upload response, implement 3-way dedup routing (new/duplicate/update), and add delivery tracking fields (`version`, `deliveryCount`, `deliveryHistory`, `lastDeliveredAt`) to Cosmos DB records.

**Independent Test**: (1) Place a new CSV file → Cosmos record created with `version: 1`, `deliveryCount: 1`. (2) Place the same file again → `deliveryCount: 2`, no SharePoint upload, `Cancelled` termination. (3) Place file with same name but different content → `version: 2`, `deliveryCount: 3`, new SharePoint upload.

### Implementation for User Story 1

- [X] T008 [US1] Reorder `logic-apps/sftp-file-ingestion/workflow.json`: move `Upload_to_blob` action to run AFTER `Parse_filename_parts` and BEFORE `Compute_dedup_key` (step 7 in the new flow order per contracts.md §3). Update `runAfter` dependencies so `Upload_to_blob` depends on `Parse_filename_parts` and `Compute_dedup_key` depends on `Upload_to_blob`.
- [X] T009 [US1] Update `Compute_dedup_key` action in `logic-apps/sftp-file-ingestion/workflow.json`: change formula from current expression to `@{base64(triggerOutputs()?['headers']?['x-ms-file-path'])}` (path only, no etag). This value is used as the Cosmos DB document `id`.
- [X] T010 [US1] Update `Check_for_duplicate` Cosmos DB action in `logic-apps/sftp-file-ingestion/workflow.json`: change partition key from `"received"` to computed `{sftpUsername}_{YYYY-MM}` value (= `@{concat(parameters('sftpUsername'),'_',formatDateTime(utcNow(),'yyyy-MM'))}`), use dedup key as document id for point-read.
- [X] T011 [US1] Implement 3-way `Handle_duplicate_check` in `logic-apps/sftp-file-ingestion/workflow.json`: when `Check_for_duplicate` succeeds (200 = existing doc found), compare `body('Upload_to_blob')?['ContentMD5']` against `body('Check_for_duplicate')?['contentHash']`. If same → true duplicate path (T012). If different → content update path (T013). When 404 → new file path (continue to `Create_intake_record`).
- [X] T012 [US1] Add `Patch_delivery_count` Cosmos DB action for the true duplicate path in `logic-apps/sftp-file-ingestion/workflow.json`: patch the existing document to increment `deliveryCount`, append `{"deliveredAt": "@{utcNow()}", "contentHash": "@{body('Upload_to_blob')?['ContentMD5']}", "action": "duplicate"}` to `deliveryHistory`, update `lastDeliveredAt` to `@{utcNow()}`. Then terminate with `Cancelled` status via `Terminate_duplicate`.
- [X] T013 [US1] Add `Patch_content_update` Cosmos DB action for the content update path in `logic-apps/sftp-file-ingestion/workflow.json`: patch the existing document to update `contentHash` to new MD5, increment `version`, increment `deliveryCount`, append `{"deliveredAt": "@{utcNow()}", "contentHash": "@{body('Upload_to_blob')?['ContentMD5']}", "action": "update"}` to `deliveryHistory`, update `lastDeliveredAt`. Then continue to downstream processing (file type routing, SharePoint upload, archive).
- [X] T014 [US1] Update `Create_intake_record` Cosmos DB action in `logic-apps/sftp-file-ingestion/workflow.json`: set document `id` to `@{outputs('Compute_dedup_key')}` (dedup key, not `sftp-{guid}`), set `partitionKey` to `@{concat(parameters('sftpUsername'),'_',formatDateTime(utcNow(),'yyyy-MM'))}`, set `intakeSource` to `"sftp"`, add fields `contentHash: "@{body('Upload_to_blob')?['ContentMD5']}"`, `version: 1`, `deliveryCount: 1`, `deliveryHistory: [{"deliveredAt": "@{utcNow()}", "contentHash": "@{body('Upload_to_blob')?['ContentMD5']}", "action": "new"}]`, `lastDeliveredAt: "@{utcNow()}"`.
- [X] T015 [US1] Deploy updated workflow to Azure via REST API PUT (`https://management.azure.com/subscriptions/.../resourceGroups/rg-docproc-dev/providers/Microsoft.Logic/workflows/logic-sftp-docproc-dev-izr2ch55woa3c?api-version=2016-06-01`) with `location: swedencentral` using the deploy script `deploy_updates.ps1`

**Checkpoint**: New files create records with `version: 1, deliveryCount: 1`. True duplicates increment `deliveryCount` only and terminate. Content updates increment both `version` and `deliveryCount`, then re-process downstream.

---

## Phase 4: User Story 4 — Dashboard Delivery Tracking (Priority: P3)

**Goal**: Dashboard displays `deliveryCount`, `version`, and `lastDeliveredAt` for SFTP records so operators can see duplicate delivery history and content update versions.

**Independent Test**: Process a file 3 times (new → duplicate → update). View dashboard. Verify record shows `version: 2`, `deliveryCount: 3`, and `lastDeliveredAt` timestamp.

### Implementation for User Story 4

- [X] T016 [US4] Add `version`, `deliveryCount`, and `lastDeliveredAt` columns to the SFTP records display in `src/webapp/templates/dashboard.html`: show version as a badge (e.g., "v2"), delivery count as a number, `lastDeliveredAt` as a relative timestamp. Only display these columns for SFTP records (`intakeSource == "sftp"`).
- [X] T017 [US4] Ensure the dashboard Cosmos DB query in `src/webapp/main.py` includes `version`, `deliveryCount`, `deliveryHistory`, and `lastDeliveredAt` fields in the SELECT projection for SFTP records

**Checkpoint**: Dashboard shows delivery tracking data for SFTP records. Email records are unaffected.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation, regression testing, documentation update

- [ ] T018 [P] Run existing unit and integration tests to verify partition key change and workflow reorder cause no regressions: `pytest tests/ -v`
- [X] T019 [P] Update `logic-apps/sftp-file-ingestion/README.md` with: revised workflow action order (14 steps per contracts.md §3), 3-way dedup routing description, content hash source (`ContentMD5` from blob upload), delivery tracking fields, partition key `/partitionKey`
- [X] T020 Run `quickstart.md` validation scenarios end-to-end: scenario 6 (duplicate detection with delivery tracking — verify `deliveryCount` increment), scenario 6a (content update detection — verify `version` increment + re-upload to SharePoint). **⚠️ Requires manual SFTP file upload — see quickstart.md §6 and §6a for step-by-step instructions.**
- [X] T021 Verify auth configurations work after partition key migration: test SFTP SSH key connection, SharePoint Graph API OAuth (client secret from Key Vault), and Cosmos DB managed identity access with new partition key. Confirm constitution exception for SharePoint client secret is documented in CAR-005.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (container must be recreated with new partition key first). **BLOCKS all user stories.**
- **User Story 1 (Phase 3)**: Depends on Phase 2 (partition key must be `/partitionKey` for dedup point-reads with partition `{sftpUsername}_{YYYY-MM}`)
- **User Story 4 (Phase 4)**: Depends on Phase 3 (delivery tracking fields must exist in Cosmos DB records)
- **Polish (Phase 5)**: Depends on all previous phases

### User Story Dependencies

```
Phase 1 (Setup: PK migration) ──► Phase 2 (Foundational: code updates) ──► Phase 3 (US1: Workflow dedup) ──► Phase 4 (US4: Dashboard) ──► Phase 5 (Polish)
```

All phases are sequential for this enhancement — each depends on the previous.

### Within Phase 3 (User Story 1)

- T008 (reorder) must be first — all subsequent tasks depend on the new action order
- T009 (dedup key) and T010 (partition key) can run in parallel after T008
- T011 (3-way routing) depends on T009 and T010
- T012 (duplicate path) and T013 (update path) depend on T011 but can run in parallel with each other
- T014 (create record) depends on T009 (uses new doc id)
- T015 (deploy) must be last

### Parallel Opportunities

**Phase 1**: T002 and T003 can run in parallel with T001 (different files).

**Phase 2**: T005, T006, T007 can run in parallel (different files). T004 is independent.

**Phase 3**: T012 and T013 can run in parallel (different branches in the same file, but editing different actions).

**Phase 4**: T016 and T017 can run in parallel (different files).

**Phase 5**: T018 and T019 can run in parallel.

---

## Implementation Strategy

### MVP First (Phase 3 Only)

1. Complete Phase 1: Recreate Cosmos container with `/intakeSource` partition key
2. Complete Phase 2: Update all code to use new partition key
3. Complete Phase 3: Workflow reorder + 3-way dedup + delivery tracking
4. **STOP and VALIDATE**: Test new file, duplicate, and content update scenarios (quickstart 6 + 6a)
5. Deploy if ready — dashboard enhancement is optional

### Incremental Delivery

1. **Increment 1** (Phases 1–2): Partition key migrated. All existing functionality works unchanged.
2. **Increment 2** (Phase 3): Content hash dedup active. True duplicates tracked. Content updates detected and re-processed.
3. **Increment 3** (Phase 4): Dashboard shows delivery tracking. Operators can see duplicate/update history.
4. **Increment 4** (Phase 5): Full regression validation and documentation update.

---

## Notes

- All tasks in Phase 3 edit `logic-apps/sftp-file-ingestion/workflow.json` — sequential editing required
- The blob upload producing an orphan blob on duplicates is acceptable (audit trail / cleanup script)
- `Generate_file_id` (`sftp-{guid}`) is retained for the blob path only — it is NOT used as Cosmos doc ID anymore
- Cosmos DB Consumption tier does not support patch operations natively — T012 and T013 may need to use read-modify-write (upsert) instead of PATCH. Verify connector capabilities.
- Deploy method: REST API PUT only (not `az logic workflow update`). Requires `location: swedencentral`.
