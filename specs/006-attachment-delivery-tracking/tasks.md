# Tasks: Attachment Delivery Tracking for Email and Download Links

**Input**: Design documents from `/specs/006-attachment-delivery-tracking/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md (dedup approach decisions), data-model.md (extended schema), contracts/ (action contracts and patch bodies)

**Context**: The SFTP intake workflow (spec 003) already implements the full 3-way dedup pattern (new / duplicate / update) with content hash extraction, delivery tracking fields, and dashboard badges. These tasks extend the same pattern to email attachments and download-link documents. No infrastructure changes are needed — the Cosmos DB container `/partitionKey` partition and existing Azure resources support this feature as-is.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: No new dependencies or project structure changes required. Existing `azure-cosmos`, `azure-storage-blob`, `azure-identity`, `FastAPI`, `Jinja2`, and `pytest` are already in place. Verify readiness.

- [x] T001 Verify existing project dependencies in `requirements.txt` — confirm `azure-cosmos`, `azure-storage-blob`, `azure-identity` are present (no new packages required per plan.md dependency gate)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the reusable `find_by_content_hash()` Cosmos DB query helper needed by the Python link download tool (US2) and available for future use. Create test scaffold.

**⚠️ CRITICAL**: US2 (Phase 4) cannot begin until this phase is complete. US1 (Phase 3) does NOT depend on this phase (Logic App uses Cosmos connector, not Python helper) and can proceed in parallel.

- [x] T002 Add `find_by_content_hash(content_hash, partition_key) -> dict | None` helper to `src/agents/tools/cosmos_tools.py` — partition-scoped SQL query `SELECT TOP 1 * FROM c WHERE c.contentHash = @contentHash` per contracts.md §3
- [x] T003 [P] Create `tests/unit/test_delivery_tracking.py` with unit tests for `find_by_content_hash()`: test returns matching record, test returns None when no match, test scopes query to partition key

**Checkpoint**: Helper function exists and unit tests pass. US2 can now use the helper.

---

## Phase 3: User Story 1 — Track Delivery of Email Attachments (Priority: P1) 🎯 MVP

**Goal**: Email Logic App extracts Content-MD5 after blob upload, performs content-hash dedup within the partition, and populates delivery tracking fields (`contentHash`, `version`, `deliveryCount`, `deliveryHistory`, `lastDeliveredAt`) on Cosmos DB records. Duplicates increment `deliveryCount` instead of creating new records.

**Independent Test**: Send two emails from the same sender domain with the same PDF attachment. First creates record with `version: 1`, `deliveryCount: 1`. Second increments `deliveryCount` to 2 on the matching record.

### Implementation for User Story 1

**Inside `For_each_attachment` loop:**

- [x] T004 [US1] Add `Get_attachment_md5` HTTP HEAD action in `logic-apps/email-ingestion/workflow.json` after `Create_blob_(V2)` — HEAD request to `https://stdocprocdevizr2ch55.blob.core.windows.net/attachments/{messageId}/{filename}` with ManagedServiceIdentity auth to extract `Content-MD5` header (same pattern as SFTP `Get_blob_md5` per research.md §R1)
- [x] T005 [US1] Update `Append_to_AttachmentPaths` compose action in `logic-apps/email-ingestion/workflow.json` to include `contentMd5` field from `outputs('Get_attachment_md5')['headers']['Content-MD5']` in each attachment path entry per data-model.md extended schema

**After `For_each_attachment` loop (dedup query + conditional routing):**

- [x] T006 [US1] Add `Compute_primary_hash` compose action in `logic-apps/email-ingestion/workflow.json` to extract the first attachment's `contentMd5` from the `AttachmentPaths` variable as the primary content hash for dedup
- [x] T007 [US1] Add `Check_content_hash_dedup` Cosmos DB query action in `logic-apps/email-ingestion/workflow.json` — query `SELECT TOP 1 * FROM c WHERE c.contentHash = @contentHash` with partition key header set to the computed `{senderDomain}_{YYYY-MM}` value, using the primary content hash per contracts.md §1
- [x] T008 [US1] Add `Handle_email_dedup` conditional action in `logic-apps/email-ingestion/workflow.json` with 3-way routing: if query returns 0 results → new record path; if ≥1 result with same hash → duplicate path; if ≥1 result with different hash (same filename) → content update path. Mirror the SFTP `Handle_duplicate_check` pattern per contracts.md §1
- [x] T009 [US1] Update `Create_intake_record` Cosmos DB upsert action body (new record path) in `logic-apps/email-ingestion/workflow.json` — add fields: `contentHash` (primary attachment MD5), `version: 1`, `deliveryCount: 1`, `deliveryHistory` array with initial `{deliveredAt, contentHash, action: "new"}` entry, `lastDeliveredAt` set to `utcNow()` per contracts.md §2
- [x] T010 [US1] Add `Patch_email_delivery_count` Cosmos DB patch action (duplicate path) in `logic-apps/email-ingestion/workflow.json` — increment `deliveryCount`, append `{deliveredAt, contentHash, action: "duplicate"}` to `deliveryHistory`, update `lastDeliveredAt` on the existing record per contracts.md §2 duplicate patch body
- [x] T011 [US1] Add `Patch_email_content_update` Cosmos DB patch action (content update path) in `logic-apps/email-ingestion/workflow.json` — update `contentHash`, increment `version` and `deliveryCount`, append `{deliveredAt, contentHash, action: "update"}` to `deliveryHistory`, update `lastDeliveredAt` per contracts.md §2 content update patch body
- [ ] T012 [US1] Deploy updated email Logic App workflow via `deploy_updates.ps1`

**Checkpoint**: New email attachments create records with `version: 1`, `deliveryCount: 1`. Duplicate attachments (same hash, same partition) increment `deliveryCount`. Content update patch action wired but filename-matching refinement deferred to US4.

---

## Phase 4: User Story 2 — Track Delivery of Download-Link Documents (Priority: P1)

**Goal**: Python link download tool computes MD5 from downloaded bytes, performs content-hash dedup via the `find_by_content_hash()` helper, and populates delivery tracking fields on Cosmos DB records for link-sourced documents.

**Independent Test**: Send two emails with download links to the same document content. First creates record with `deliveryCount: 1`. Second increments `deliveryCount` to 2.

### Implementation for User Story 2

- [x] T013 [US2] In `src/agents/tools/link_download_tool.py`: add `content_md5: str | None = None` field to the `DownloadedFile` dataclass per contracts.md §4. After downloading file bytes, compute MD5 using `hashlib.md5(file_data).digest()` and base64-encode it. Set `content_md5` on the result.
- [x] T014 [US2] In `src/agents/tools/link_download_tool.py`: after blob upload, call `find_by_content_hash(content_md5, partition_key)` from cosmos_tools.py to check for existing record with matching hash in the same partition
- [x] T015 [US2] In `src/agents/tools/link_download_tool.py`: implement 3-way dedup routing — if no match: populate new record with `contentHash`, `version: 1`, `deliveryCount: 1`, `deliveryHistory [{action: "new"}]`, `lastDeliveredAt`; if hash match: patch existing record incrementing `deliveryCount` with `action: "duplicate"`; if failed download: skip delivery tracking per FR-011. Add structured logging for dedup decisions per CAR-008
- [x] T016 [P] [US2] Add unit tests in `tests/unit/test_delivery_tracking.py` for link download delivery tracking: test new record gets all tracking fields, test duplicate increments deliveryCount, test failed download skips tracking

**Checkpoint**: Link-sourced documents have full delivery tracking. Duplicates are detected within the same partition.

---

## Phase 5: User Story 3 — Dashboard Shows Delivery Badges for All Intake Sources (Priority: P2)

**Goal**: Dashboard displays version badges (v1, v2) and delivery count indicators (2x, 3x) for records from any intake channel — email, download link, or SFTP — wherever `version` and `deliveryCount` fields are populated.

**Independent Test**: Process an email with a tracked attachment. View dashboard. Verify version badge and delivery count appear for the email record, same as SFTP records.

### Implementation for User Story 3

- [x] T017 [US3] In `src/webapp/templates/dashboard.html` (~line 219): change guard from `{% if email.intakeSource == 'sftp' %}` to `{% if email.version is defined and email.version is not none %}` per contracts.md §5 and research.md §R5. **Responsive validation**: confirm badge column renders correctly at mobile breakpoints (existing responsive layout, no new elements).
- [x] T018 [P] [US3] Verify dashboard route query in `src/webapp/main.py` returns all delivery tracking fields (`contentHash`, `version`, `deliveryCount`, `deliveryHistory`, `lastDeliveredAt`) for email records — should already work via `SELECT *` per research.md §R6, no code change expected

**Checkpoint**: Dashboard shows version and delivery badges for email and link records. Legacy records without tracking fields still show "—".

---

## Phase 6: User Story 4 — Content Update Detection for Email Attachments (Priority: P3)

**Goal**: When the same sender sends an attachment with the same filename but different content (different hash), the system increments `version` and `deliveryCount` and records the action as `"update"` in delivery history. Extends the basic hash-matching dedup from US1 with filename-based update detection. Dedup compares only current `contentHash`, not historical values (per FR-004 / Clarifications).

**Independent Test**: Send two emails from the same sender domain with the same filename but different file content. Second email triggers version increment (v1 → v2) with `action: "update"` in history.

### Implementation for User Story 4

- [x] T019 [US4] Refine `Handle_email_dedup` conditional in `logic-apps/email-ingestion/workflow.json` — when `Check_content_hash_dedup` returns 0 results, add a secondary filename-match query or extend the conditional to check for existing records with matching filename within the same partition, then route to `Patch_email_content_update` (T011) per FR-004
- [x] T020 [US4] Add filename-based content update detection in link download dedup logic in `src/agents/tools/link_download_tool.py` — after `find_by_content_hash()` returns None, query for existing record with same filename in partition; if found with different hash, patch as content update (increment `version` + `deliveryCount`, `action: "update"`)
- [x] T021 [P] [US4] Add unit test in `tests/unit/test_delivery_tracking.py` for content update version increment: test same-filename-different-hash increments version
- [ ] T022 [US4] Deploy updated Logic App and webapp via `deploy_updates.ps1`

**Checkpoint**: Same-filename-different-content documents correctly increment `version`. Both Logic App and Python tool handle the update path.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation, regression testing, documentation

- [x] T023 [P] Run existing unit and integration tests to verify no regressions: `pytest tests/ -v`
- [ ] T024 Run quickstart.md validation scenarios end-to-end: Test 1 (new email record), Test 2 (duplicate detection), Test 3 (content update), Test 4 (dashboard badges), Test 5 (link download tracking). **⚠️ Requires sending test emails to monitored inbox — see quickstart.md**
- [ ] T025 [P] Verify SFTP delivery tracking continues working after dashboard guard change (regression check) — process an SFTP file and confirm badges still render
- [ ] T026 [SC-005] Verify dashboard page load time does not increase measurably (≤100ms increase) after badge guard change — compare load times with and without delivery tracking fields present using browser dev tools or `curl` timing

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: No dependencies — can start immediately after Setup
- **User Story 1 (Phase 3)**: No dependency on Phase 2 (Logic App uses Cosmos connector, not Python helper). **Can run in parallel with Phase 2.**
- **User Story 2 (Phase 4)**: Depends on Phase 2 (needs `find_by_content_hash()` helper)
- **User Story 3 (Phase 5)**: Depends on Phase 3 or 4 (needs delivery tracking fields populated on email/link records to verify badges)
- **User Story 4 (Phase 6)**: Depends on Phase 3 (extends email Logic App dedup) and Phase 4 (extends Python dedup logic)
- **Polish (Phase 7)**: Depends on all previous phases

### User Story Dependencies

```
Phase 1 (Setup) ──► Phase 2 (Foundational: helper) ──► Phase 4 (US2: Link download) ──┐
                                                                                        ├──► Phase 6 (US4: Content updates) ──► Phase 7 (Polish)
                    Phase 3 (US1: Email Logic App) ──► Phase 5 (US3: Dashboard badges) ┘
```

**US1 and Foundational can proceed in parallel** (different files: Logic App vs Python). US3 can start once either US1 or US2 has records with delivery tracking. US4 depends on both US1 and US2.

### Within Phase 3 (User Story 1)

- T004 and T005 (inside attachment loop) must come first
- T006 (compute primary hash) depends on T005
- T007 (dedup query) depends on T006
- T008 (conditional routing) depends on T007
- T009, T010, T011 (three branch actions) depend on T008, sequential in same file
- T012 (deploy) must be last

### Parallel Opportunities

**Phase 2 + Phase 3**: Entire phases can run in parallel (Python helper vs Logic App — no dependencies).

**Phase 4**: T016 (tests) can run in parallel with T013-T015 implementation if test-first approach is used.

**Phase 5**: T017 and T018 can run in parallel (template file vs route verification).

**Phase 7**: T023 and T025 can run in parallel (different test scopes).

---

## Parallel Example: User Story 1

```bash
# Phases 2 + 3 in parallel (different files, no cross-dependency):
# Thread A: T002 (cosmos_tools.py helper) → T003 (unit tests)
# Thread B: T004 → T005 → T006 → T007 → T008 → T009 → T010 → T011 → T012 (workflow.json)
```

## Parallel Example: User Story 3

```bash
# Both tasks in parallel (different files):
# Thread A: T017 (dashboard.html guard change)
# Thread B: T018 (main.py route verification)
```

---

## Implementation Strategy

### MVP First (Phase 1 → Phases 2+3 in parallel → Phase 5)

1. Complete Phase 1: Setup verification
2. **Start Phases 2 + 3 in parallel**: Build Python helper AND email Logic App dedup simultaneously
3. Complete Phase 4: Link download delivery tracking
4. Complete Phase 5: Dashboard badge guard change
5. **STOP and VALIDATE**: Run quickstart Tests 1, 2, 4, 5 (new record, duplicate, badges, link download)
6. Deploy if ready — content update detection (US4) is a P3 enhancement

### Incremental Delivery

1. **Increment 1** (Phases 2 + 3): Email attachments have delivery tracking. Duplicates detected. Python dedup helper ready.
2. **Increment 2** (Phase 4): Download-link documents also tracked. Both primary channels covered.
3. **Increment 3** (Phase 5): Dashboard shows badges universally. Operators see tracking for all sources.
4. **Increment 4** (Phase 6): Content update detection active. Same-filename-different-content handled.
5. **Increment 5** (Phase 7): Full regression validation and end-to-end quickstart verification.

---

## Notes

- All US1 tasks (T004–T011) edit `logic-apps/email-ingestion/workflow.json` — sequential editing required within the file
- The email Logic App uses Cosmos DB managed connector actions (not Python SDK) — dedup query uses `x-ms-documentdb-raw-partitionkey` header for partition scoping
- Content-MD5 is retrieved via HTTP HEAD after blob upload (same proven pattern as SFTP), not from the managed connector response (research.md §R1)
- For emails with multiple attachments, primary `contentHash` is from the first attachment; per-attachment MD5 is captured in `attachmentPaths[].contentMd5` (data-model.md)
- Cross-channel dedup (same doc via email AND SFTP) is explicitly out of scope — each channel has independent tracking
- Legacy email records without delivery tracking fields continue to show "—" on dashboard — no backfill required
- Race condition on concurrent dedup accepted — negligible window, next-delivery dedup catches rare duplicates (Clarifications §Session 2026-03-30)
- Re-delivery of old hash after version update treated as content update (v3) — dedup compares current hash only (FR-004)
