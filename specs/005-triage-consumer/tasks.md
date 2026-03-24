# Tasks: Triage Consumer Client

**Input**: Design documents from `/specs/005-triage-consumer/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Not explicitly requested in feature specification. Unit tests included in Polish phase per CAR-007 risk-based testing guidance.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependency management, and environment configuration

- [x] T001 Create project file structure: `src/triage_consumer.py`, `utils/send_test_triage_message.py`, `TRIAGE_CONSUMER.md`
- [x] T002 Add `requests>=2.31.0` to `requirements.txt` per research decision R-005
- [x] T003 [P] Add environment variable loading from `.env01` using `python-dotenv` with all config variables defined in `contracts/cli-contract.md` (SERVICEBUS_NAMESPACE, TRIAGE_COMPLETE_SB_NAMESPACE, TRIAGE_COMPLETE_QUEUE, API_ENDPOINT, DATA_MODEL_NAME, DEFAULT_PROJECT_NAME, DEFAULT_ANALYSIS_NAME, DEFAULT_LANGUAGE, STORAGE_ACCOUNT_URL) in `src/triage_consumer.py`
- [x] T004 [P] Set up Service Bus client connection using `DefaultAzureCredential` with namespace resolution (TRIAGE_COMPLETE_SB_NAMESPACE fallback to SERVICEBUS_NAMESPACE) and verify credential acquisition in `src/triage_consumer.py`

**Checkpoint**: Project scaffolded with dependencies and configuration ready

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T005 Implement `process_message()` orchestrator function that parses JSON from Service Bus message body, delegates to display and API functions, and completes the message in `src/triage_consumer.py`
- [x] T006 Implement malformed message handling: wrap JSON parsing in try/except, log parse errors, and acknowledge message to prevent queue poisoning (FR-010, Edge Case 1) in `src/triage_consumer.py`
- [x] T007 [P] Implement `format_file_size()` helper for human-readable attachment sizes in `src/triage_consumer.py`

**Checkpoint**: Foundation ready — message receive-parse-complete pipeline works, user story implementation can begin

---

## Phase 3: User Story 1 — Monitor Triaged Documents in Real Time (Priority: P1) 🎯 MVP

**Goal**: Continuously listen to the triage-complete queue and display formatted document information in the terminal as messages arrive.

**Independent Test**: Send a sample message to the triage-complete queue using `utils/send_test_triage_message.py` and verify the consumer displays document ID, intake source, subject, sender, attachments, relevance score, category, and routing information in a readable format.

### Implementation for User Story 1

- [x] T008 [US1] Implement `print_message_details()` for email-sourced messages: display document ID, subject, sender, receivedAt, processedAt, attachment count, attachment names/links/sizes, relevance confidence score, initial category, reasoning, pipeline mode, status, and routing path in `src/triage_consumer.py`
- [x] T009 [US1] Extend `print_message_details()` for SFTP-sourced messages: display originalFilename, fileType, and blobPath in addition to standard fields (FR-003) in `src/triage_consumer.py`
- [x] T010 [US1] Handle both attachment formats in display: dictionary objects (with `local_link`/`blobUrl`/`path` URL resolution priority per data-model.md) and plain strings (Edge Case 4) in `src/triage_consumer.py`
- [x] T011 [US1] Handle messages with no attachments: display document info without attachment section (Edge Case 2) in `src/triage_consumer.py`
- [x] T012 [US1] Implement `run_consumer_loop()` with continuous `while True` loop using `receiver.receive_messages(max_message_count=1, max_wait_time=30)` per research decision R-001 in `src/triage_consumer.py`
- [x] T013 [US1] Implement startup banner displaying namespace, queue name, API endpoint, and "waiting for messages" prompt per `contracts/cli-contract.md` output format in `src/triage_consumer.py`
- [x] T014 [US1] Implement graceful shutdown on KeyboardInterrupt (Ctrl+C): catch signal, close Service Bus client, log shutdown message, exit code 0 (FR-009) in `src/triage_consumer.py`
- [x] T015 [US1] Add connection error handling at startup: display clear error message and exit code 1 when Service Bus namespace is unreachable (Edge Case 3) in `src/triage_consumer.py`
- [x] T016 [US1] Add mid-operation connection loss handling: log error and attempt reconnect on next loop iteration (Edge Case 5) in `src/triage_consumer.py`

**Checkpoint**: Consumer connects to queue, displays formatted document details for both email and SFTP messages, handles edge cases, and shuts down gracefully. Fully functional as standalone MVP.

---

## Phase 4: User Story 2 — Forward Triaged Documents to an External API (Priority: P2)

**Goal**: Transform each consumed message into an API request payload and submit it to a configurable document processing endpoint.

**Independent Test**: Run the consumer with a configured API endpoint, send a message to the queue, and verify the outbound request payload contains correct document SAS URLs, project name, analysis name, data model name, language, and metadata per data-model.md API Request Payload schema.

### Implementation for User Story 2

- [x] T017 [US2] Implement `extract_sas_url_from_attachment()` to extract URL from attachment objects using resolution priority (`local_link` → `blobUrl` → `path`) and handle string-format attachments in `src/triage_consumer.py`
- [x] T018 [US2] Implement `build_api_request()` to transform triage message into API request payload per data-model.md: documents array (sas_url + document_name per attachment), project_name (fund name heuristic from subject), analysis_name, analysis_description, data_model_name, classifier_name (null), language (detect from content), created_by, auto_extract, _metadata (email_id, intake_source, processed_at) in `src/triage_consumer.py`
- [x] T019 [US2] Implement fund name extraction heuristic: scan subject/body for "Fonds"/"Fund" keywords; extract surrounding words as project_name; fall back to DEFAULT_PROJECT_NAME in `src/triage_consumer.py`
- [x] T020 [US2] Implement language detection: check relevance reasoning and subject for French indicators; default to DEFAULT_LANGUAGE in `src/triage_consumer.py`
- [x] T021 [US2] Implement `call_api()` with `requests.post()`: JSON payload, 30-second timeout, success/failure logging (FR-005) in `src/triage_consumer.py`
- [x] T022 [US2] Wire API forwarding into `process_message()`: parse → display → build API request → call API → complete message. Always complete message regardless of API outcome (FR-006, R-002) in `src/triage_consumer.py`
- [x] T023 [US2] Handle empty documents list: when message has no attachments, send API request with empty documents array (Edge Case 2) in `src/triage_consumer.py`

**Checkpoint**: Consumer processes messages end-to-end: queue → display → API forward → acknowledge. API payload matches data-model.md schema with snake_case naming convention.

---

## Phase 5: User Story 3 — Send Test Messages for Development (Priority: P3)

**Goal**: Provide a utility to send realistic sample messages to the triage-complete queue for testing without depending on the upstream pipeline.

**Independent Test**: Run the utility, select a message type (email or SFTP), and verify the message appears in the triage-complete queue with correct structure matching the triage message schema from data-model.md.

### Implementation for User Story 3

- [x] T024 [US3] Create `utils/send_test_triage_message.py` with environment variable loading from `.env01` (same Service Bus config as consumer per contracts/cli-contract.md)
- [x] T025 [US3] Implement email sample message builder: realistic triage message with camelCase field names matching data-model.md schema (emailId, from, subject, receivedAt, hasAttachments, attachmentsCount, attachmentPaths with object format, intakeSource="email", relevance, pipelineMode, status, processedAt, routing) in `utils/send_test_triage_message.py`
- [x] T026 [US3] Implement SFTP sample message builder: realistic triage message with SFTP-specific fields (originalFilename, fileType, blobPath) in addition to standard fields in `utils/send_test_triage_message.py`
- [x] T027 [US3] Implement interactive prompt for message type selection (1=Email, 2=SFTP) and Service Bus send with confirmation display per contracts/cli-contract.md in `utils/send_test_triage_message.py`

**Checkpoint**: Test utility sends realistic messages to the queue. Consumer (if running) receives and displays them within normal polling interval.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, tests, and cleanup that affect multiple user stories

- [x] T028 [P] Create usage documentation in `TRIAGE_CONSUMER.md` at repo root: overview, prerequisites, environment variables, usage instructions, test utility guide, output format examples per quickstart.md
- [x] T029 [P] Create unit tests for `build_api_request()` in `tests/unit/test_triage_consumer.py`: email message transform, SFTP message transform, empty attachments, fund name extraction, language detection, and verify output documents count matches input attachmentPaths count (SC-003)
- [x] T030 [P] Create unit tests for message parsing edge cases in `tests/unit/test_triage_consumer.py`: malformed JSON handling, mixed attachment formats (dict + string), missing optional fields
- [x] T031 Run quickstart.md validation: verify end-to-end flow (start consumer → send test message → see output → verify API call logged)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational (Phase 2) — no other story dependencies
- **User Story 2 (Phase 4)**: Depends on Foundational (Phase 2) — can run in parallel with US1 but naturally extends US1's `process_message()` flow
- **User Story 3 (Phase 5)**: Depends on Setup (Phase 1) only — different file (`utils/`), can start after Setup
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) — no dependencies on other stories. Delivers standalone MVP.
- **User Story 2 (P2)**: Depends on US1's `process_message()` orchestrator (T005) from Foundational phase. Can wire into US1's flow independently. No dependency on US3.
- **User Story 3 (P3)**: Independent of US1 and US2 — operates on a separate file (`utils/send_test_triage_message.py`). Only needs Service Bus config from Setup phase.

### Within Each User Story

- Display functions (US1) before API transform functions (US2)
- Core happy path before edge case handling
- Story complete before moving to next priority

### Parallel Opportunities

- T003 + T004 can run in parallel (different concerns in same file, no overlap)
- T008 + T009 are sequential (same function, SFTP extends email display)
- T017 + T018 + T019 + T020 can be partially parallelized (helper functions in same file)
- T024–T027 (US3) can run in parallel with US1/US2 (different file)
- T028 + T029 + T030 can all run in parallel (different files)

---

## Parallel Example: User Story 3 alongside User Story 1

```text
# These can run concurrently (different files, no shared dependencies):

Thread A (src/triage_consumer.py):
  T008 → T009 → T010 → T011 → T012 → T013 → T014 → T015 → T016

Thread B (utils/send_test_triage_message.py):
  T024 → T025 → T026 → T027
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001–T004)
2. Complete Phase 2: Foundational (T005–T007)
3. Complete Phase 3: User Story 1 (T008–T016)
4. **STOP and VALIDATE**: Run consumer, send a message manually or via test utility, verify formatted output
5. Deploy/demo if ready — consumer displays triaged documents in real time

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add User Story 1 → Consumer displays messages → Validate independently (MVP!)
3. Add User Story 2 → Consumer forwards to API → Validate API payloads
4. Add User Story 3 → Test utility available → Validate end-to-end loop
5. Polish → Documentation + unit tests → Final validation via quickstart.md

### Single Developer Strategy (Recommended)

Since all consumer code lives in one file (`src/triage_consumer.py`):

1. Complete Setup (T001–T004)
2. Complete Foundational (T005–T007)
3. Complete US1 sequentially (T008–T016) — builds display capability
4. Complete US2 sequentially (T017–T023) — extends with API forwarding
5. Complete US3 in parallel or after US1+US2 (T024–T027) — separate file
6. Polish (T028–T031)

---

## Notes

- [P] tasks = different files or independent functions with no dependencies
- [Story] label maps task to specific user story for traceability
- All triage message field names use **camelCase** (existing producer contract)
- API request payload field names use **snake_case** (new consumer-owned boundary)
- `attachmentPaths` element objects have mixed conventions from multiple upstream sources — consumer handles all variants defensively
- Total: 31 tasks across 6 phases
