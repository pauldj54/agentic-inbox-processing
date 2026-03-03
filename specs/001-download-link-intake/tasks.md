# Tasks: Download-Link Intake

**Input**: Design documents from `/specs/001-download-link-intake/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/contracts.md, quickstart.md

**Tests**: Explicitly requested via CAR-007 — focused tests covering link detection, successful download, and failure handling.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add new dependency and create test infrastructure

- [x] T001 [P] Add `azure-storage-blob>=12.19.0` to requirements.txt and record dependency justification (official Microsoft SDK required for blob upload from Python — see plan.md Constitution Check, Dependency gate)
- [x] T002 [P] Create test directory structure: `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, and add `[tool.pytest.ini_options]` section to pyproject.toml (create pyproject.toml if absent) with `testpaths = ["tests"]`
- [x] T003 [P] Verify blob storage auth: confirm `Storage Blob Data Contributor` role is assigned for the app identity on storage account `stdocprocdevizr2ch55`, and test `DefaultAzureCredential` connectivity by running a minimal blob list operation against the `attachments` container (constitution Delivery Workflow gate #3: explicit auth verification)

---

## Phase 2: Foundational (Schema Migration)

**Purpose**: Migrate `attachmentPaths` from `string[]` to `object[]` (`{path, source}`) across all components. This is a breaking change — all consumers must be updated together.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T004 [P] Update `Append_to_AttachmentPaths` action value from string interpolation `"@{triggerOutputs()?['body/id']}/@{item()?['name']}"` to object `{"path": "@{triggerOutputs()?['body/id']}/@{item()?['name']}", "source": "attachment"}` in logic-apps/email-ingestion/workflow.json (see research.md §4 for exact before/after)
- [x] T005 [P] Update the regex-based `attachmentPaths` parser in `_extract_email_data_regex()` to handle JSON objects instead of flat strings — replace `re.findall(r'"([^"]+)"', paths_content)` with proper object extraction that produces `[{"path": "...", "source": "..."}]`, and update the returned dict to include the object format in src/agents/tools/queue_tools.py (lines 205-225)
- [x] T006 [P] Update `attachment_paths` mapping in the email body parser (line 203: `"attachment_paths": email_body.get("attachmentPaths", [])`) to pass through object format unchanged in src/agents/tools/graph_tools.py
- [x] T007 Update all four `attachmentPaths` iteration sites to use backward-compatible reading (`isinstance(entry, str)` check per data-model.md migration pattern) — lines 424, 520, 594, and 675 in src/agents/email_classifier_agent.py. Each site extracts filenames via `path.split("/")` and must now read from `entry.get("path")` for object entries.

**Checkpoint**: Schema migration complete — all consumers handle both legacy string and new object `{path, source}` formats

---

## Phase 3: User Story 1 — Detect and Download Linked Document (Priority: P1) 🎯 MVP

**Goal**: Detect document download links in email bodies, fetch linked files via HTTPS, store in Azure Blob Storage, and update Cosmos DB with enriched attachment metadata.

**Independent Test**: Send an email with no traditional attachments but with a body containing a single valid download link pointing to a PDF. Verify the document is downloaded, stored at `/attachments/{emailId}/{filename}`, Cosmos DB record includes the attachment with `source: "link"`, and the email reaches the classification pipeline.

### Implementation for User Story 1

- [x] T008 [US1] Create `LinkDownloadTool` class with: (1) dataclasses `DownloadedFile`, `DownloadFailure`, `LinkDownloadResult` per contracts/contracts.md §3; (2) URL detection via regex `https?://[^\s"'<>)\]]+` for plain text and `href` extraction for HTML bodies using `html.parser` stdlib; (3) document extension filter `\.(pdf|docx?|xlsx?|csv|pptx?|txt|zip)(\?.*)?$` per research.md §1; (4) filename derivation (Content-Disposition → URL path → generated fallback); (5) async HTTP download via `aiohttp` with `ClientTimeout(total=30)` and streaming `iter_chunked(8192)` with 50MB byte counter; (6) async blob upload via `azure.storage.blob.aio.BlobServiceClient` with `DefaultAzureCredential` and `ContentSettings`; (7) `process_email_links(email_id, email_body) -> LinkDownloadResult` orchestration method; (8) configuration from env vars `STORAGE_ACCOUNT_URL`, `LINK_DOWNLOAD_MAX_SIZE_MB`, `LINK_DOWNLOAD_TIMEOUT_S` — in src/agents/tools/link_download_tool.py and add import to src/agents/tools/__init__.py
- [x] T009 [US1] Integrate link-download pre-processing step: after receiving Service Bus message and before calling `process_attachments()`, instantiate `LinkDownloadTool`, call `process_email_links(email_id, email_body)`, merge `downloaded_files` into the email's `attachmentPaths` array as `{"path": ..., "source": "link"}` objects, and update `hasAttachments`/`attachmentsCount` in src/agents/email_classifier_agent.py
- [x] T010 [US1] Update Cosmos DB email document upsert to persist enriched `attachmentPaths` (with link-sourced entries), update `hasAttachments`/`attachmentsCount` fields, and include the optional `downloadFailures` array (empty by default, populated in US2) after link download enrichment in src/agents/tools/cosmos_tools.py. *Note: T013 (US2) extends this same upsert to populate `downloadFailures` from `LinkDownloadResult.failures` — both tasks modify cosmos_tools.py sequentially.*
- [x] T011 [P] [US1] Write unit tests for: URL regex extraction from plain text and HTML bodies, document extension filtering (match `.pdf`, `.docx`, reject `.html`, `.jpg`), filename derivation from Content-Disposition header / URL path / generated fallback, and non-document domain skipping — in tests/unit/test_link_download_tool.py

**Checkpoint**: Emails with download links are processed end-to-end — linked documents stored in blob, Cosmos DB updated, email reaches classification pipeline

---

## Phase 4: User Story 2 — Graceful Handling of Unavailable or Invalid Links (Priority: P2)

**Goal**: Handle download failures (HTTP 404/500, timeout, non-document content-type, oversized files) gracefully — email processing continues unblocked, failures are logged with diagnostic context and persisted in Cosmos DB.

**Independent Test**: Send an email with a broken download link (e.g., URL returning 404). Verify the email is still ingested into Cosmos DB and forwarded to the Service Bus queue, with `downloadFailures` recorded and structured logs emitted.

### Implementation for User Story 2

- [x] T012 [P] [US2] Enhance `LinkDownloadTool` failure handling: add categorized error types (HTTP status errors, `asyncio.TimeoutError`, content-type rejection for non-document MIME types like `text/html`, file-size exceeded), add structured logging via `logging` module for every download attempt/success/failure with context (URL, email ID, HTTP status, error type, elapsed time) per FR-008 and CAR-008, and ensure `DownloadFailure` entries include ISO 8601 `attemptedAt` timestamps — in src/agents/tools/link_download_tool.py. *Note: T008 (US1) implements the happy-path download with a minimal try/except that returns an empty `failures` list on unexpected errors. This task adds categorized error classification, structured logging, and explicit timeout/size/content-type rejection.*
- [x] T013 [P] [US2] Populate `downloadFailures` in the Cosmos DB email document upsert — wire `LinkDownloadResult.failures` into the upsert already scaffolded by T010 in src/agents/tools/cosmos_tools.py. *Note: builds on T010's changes in the same file.*
- [x] T014 [P] [US2] Write unit tests for failure scenarios: HTTP 404 and 500 responses return `DownloadFailure`, download timeout after configured seconds returns `DownloadFailure`, HTML content-type response is rejected and logged, file exceeding 50MB size limit is skipped with `DownloadFailure`, and email processing continues with partial results — in tests/unit/test_link_download_tool.py

**Checkpoint**: Download failures do not block email processing — failures persisted in Cosmos DB `downloadFailures` array, structured logs emitted, email continues to classification

---

## Phase 5: User Story 3 — Dashboard Visibility for Link-Sourced Attachments (Priority: P3)

**Goal**: Admins can see whether an email's attachment was sourced from a download link versus a traditional attachment on the web dashboard.

**Independent Test**: Process an email with a link-sourced attachment, open the dashboard at `http://localhost:8000`, verify the attachment entry shows a visual indicator distinguishing link-sourced from traditional attachments.

### Implementation for User Story 3

- [x] T015 [US3] Update attachment display logic to read `source` field from `attachmentPaths` objects (with backward-compatible `isinstance(entry, str)` fallback for legacy data), and pass both `path` and `source` to the Jinja2 template context in src/webapp/main.py
- [x] T016 [US3] Add source indicator on attachment entries: display a small icon or label (`🔗 link` vs `📎 attachment`) next to each attachment name using the `source` field, ensuring the indicator is inline and responsive on mobile viewports per CAR-002 and CAR-003 — in src/webapp/templates/dashboard.html

**Checkpoint**: Dashboard shows attachment source origin — operators can distinguish link-sourced from traditional attachments at a glance (SC-003: within 5 seconds)

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Integration testing and end-to-end validation across all stories

- [x] T017 [P] Write integration test for end-to-end link download flow: mock aiohttp responses (success + failure), mock or use emulated blob storage, verify `LinkDownloadResult` contains correct `downloaded_files` and `failures`, verify Cosmos DB document would be updated with enriched `attachmentPaths` and `downloadFailures` — in tests/integration/test_link_download_flow.py
- [x] T018 Run quickstart.md validation scenarios: execute `pytest tests/unit/test_link_download_tool.py -v`, perform manual E2E test with a real download link, verify failure handling with a broken link, confirm dashboard source indicators render correctly, and measure latency delta for an email with a download link vs. without to validate SC-001 (< 30s additional)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (needs `azure-storage-blob` in requirements, test dirs for later phases) — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 (schema migration must be done first)
- **US2 (Phase 4)**: Depends on Phase 3 (enhances the `LinkDownloadTool` built in US1)
- **US3 (Phase 5)**: Depends on Phase 2 only (uses object-format `attachmentPaths` — can run in parallel with US1/US2)
- **Polish (Phase 6)**: Depends on Phases 3 and 4 (integration test covers both success and failure paths)

### User Story Dependencies

- **US1 (P1)**: Requires Foundational (Phase 2) — no dependency on other stories
- **US2 (P2)**: Requires US1 (Phase 3) — enhances `LinkDownloadTool` with robust failure handling
- **US3 (P3)**: Requires Foundational (Phase 2) only — can run in parallel with US1/US2

### Within Each User Story

- Implementation tasks in listed order (later tasks depend on earlier ones within phase)
- Tasks marked [P] can run in parallel with other same-phase tasks
- Tests can run in parallel with integration tasks (different files)

### Parallel Opportunities

- **Phase 1**: T001 ∥ T002 ∥ T003 (independent tasks)
- **Phase 2**: T004 ∥ T005 ∥ T006 (different files, same schema change applied independently), then T007
- **Phase 3 ∥ Phase 5**: US3 (T015, T016) can run in parallel with US1 (T008–T011) since they modify different files and both only depend on Phase 2
- **Within US1**: T011 can start in parallel with T008 (test file vs implementation file)
- **Within US2**: T012 ∥ T014 (different files); T013 depends on T010 (same file: cosmos_tools.py)

---

## Parallel Example: User Story 1

```text
# After Phase 2 is complete, launch in parallel:
T008: "Create LinkDownloadTool in src/agents/tools/link_download_tool.py"
T011: "Write unit tests in tests/unit/test_link_download_tool.py"

# Once T008 completes, sequentially:
T009: "Integrate pre-processing in src/agents/email_classifier_agent.py"
T010: "Update Cosmos upsert in src/agents/tools/cosmos_tools.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (3 tasks)
2. Complete Phase 2: Foundational schema migration (4 tasks)
3. Complete Phase 3: User Story 1 — link download (4 tasks)
4. **STOP and VALIDATE**: Test US1 independently per quickstart.md §2
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → Schema migration done (7 tasks)
2. Add User Story 1 → Test → Deploy (**MVP**: emails with download links processed end-to-end)
3. Add User Story 2 → Test → Deploy (failure handling + `downloadFailures` in Cosmos DB)
4. Add User Story 3 → Test → Deploy (dashboard shows link vs attachment origin)
5. Polish → Integration tests + full validation (2 tasks)
6. Each story adds value without breaking previous stories

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Backward-compatible `attachmentPaths` reading (see data-model.md migration pattern) must be maintained for one release cycle
- All `source` values are exactly `"attachment"` or `"link"` — no other values
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
