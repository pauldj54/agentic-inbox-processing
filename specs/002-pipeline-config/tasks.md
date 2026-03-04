# Tasks: Pipeline Configuration

**Input**: Design documents from `/specs/002-pipeline-config/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Tests are included per CAR-007 — two core tests explicitly required by the specification.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Verify pre-conditions and confirm no new dependencies

- [X] T001 Confirm zero new dependencies per plan.md dependency gate — verify all required packages (`azure-servicebus`, `azure-identity`, `python-dotenv`) are already in requirements.txt

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared infrastructure changes that MUST be complete before any user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T002 Add PIPELINE_MODE, TRIAGE_COMPLETE_QUEUE, and TRIAGE_COMPLETE_SB_NAMESPACE env var loading and validation to load_environment() in src/agents/run_agent.py — validate PIPELINE_MODE against {"full", "triage-only"}, default to "full" if unset (log warning) or invalid (log error + set os.environ["PIPELINE_MODE"] = "full"), read TRIAGE_COMPLETE_QUEUE with default "triage-complete", read optional TRIAGE_COMPLETE_SB_NAMESPACE
- [X] T003 [P] Extend QueueTools.__init__() with triage_queue and triage_sb_namespace parameters; add lazy _get_triage_sync_client() method using DefaultAzureCredential in src/agents/tools/queue_tools.py — per research §2 implementation pattern
- [X] T004 [P] Add pipelineMode (string) and stepsExecuted (string[]) fields to update_email_classification() in src/agents/tools/cosmos_tools.py — write in the common section before upsert_item() alongside existing updatedAt field, per data-model.md field definitions

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Full Classification Pipeline (Priority: P1) 🎯 MVP

**Goal**: Existing full-pipeline behavior continues to work unchanged. Pipeline mode is recorded on each email for audit and dashboard display. Backward-compatible default when PIPELINE_MODE is unset.

**Independent Test**: Leave PIPELINE_MODE unset (or set to "full"). Send an email to the intake queue. Verify it progresses through relevance check, attachment OCR, classification, and arrives in archival-pending or human-review with pipelineMode="full" and stepsExecuted populated.

### Implementation for User Story 1

- [X] T005 [US1] Add pipeline_mode instance attribute to EmailClassificationAgent.__init__() reading from os.getenv("PIPELINE_MODE", "full") in src/agents/email_classifier_agent.py
- [X] T006 [US1] Insert pipeline mode conditional branch after Step 2 (attachment processing, line ~353) in process_next_email(); full-mode path continues existing flow unchanged and passes pipelineMode="full" and stepsExecuted=["triage", "pre-processing", "classification", "routing"] to update_email_classification() in src/agents/email_classifier_agent.py
- [X] T007 [US1] Write test_full_mode_runs_classification test in tests/unit/test_pipeline_config.py — mock PIPELINE_MODE="full", verify classification step IS called, verify email routes to archival-pending or human-review, using existing test patterns (class-per-feature, MagicMock, patch.dict)

**Checkpoint**: Full-pipeline mode works, records pipelineMode and stepsExecuted. Backward compatible — no regression.

---

## Phase 4: User Story 2 — Triage-Only Pipeline (Priority: P1)

**Goal**: Triage-only mode skips classification (Steps 3–5) and routes relevant emails to the configurable triage-complete queue. Supports external Service Bus namespace for IDP integration. Non-relevant and low-confidence emails still route to discarded and human-review queues respectively.

**Independent Test**: Set PIPELINE_MODE=triage-only. Send a PE-relevant email to the intake queue. Verify it passes through relevance triage and attachment pre-processing but does NOT undergo classification. Verify it arrives in the triage-complete queue with relevance details but no classification category.

### Implementation for User Story 2

- [X] T008 [US2] Implement send_to_triage_queue() method in QueueTools: send triage-complete message using _get_triage_sync_client(), catch external namespace failures and route to dead-letter queue on primary namespace with error context, in src/agents/tools/queue_tools.py — per contracts §3 and research §6
- [X] T009 [US2] Implement triage-only branch in process_next_email(): build triage-complete message per contracts §1 schema (emailId, from, subject, receivedAt, attachmentPaths, relevance block, pipelineMode, routing), call queue_tools.send_to_triage_queue(), update Cosmos with pipelineMode="triage-only" and stepsExecuted=["triage", "pre-processing", "routing"], return early (skip Steps 3–5) in src/agents/email_classifier_agent.py
- [X] T010 [US2] Write test_triage_only_skips_classification test in tests/unit/test_pipeline_config.py — mock PIPELINE_MODE="triage-only", verify classification step is NOT called, verify email is sent to triage-complete queue, using existing test patterns (class-per-feature, MagicMock, patch.dict)

**Checkpoint**: Both pipeline modes work. Triage-only skips classification and routes to triage-complete. Full mode is unchanged.

---

## Phase 5: User Story 3 — Admin Modifies Pipeline Configuration (Priority: P2)

**Goal**: Operators can confirm the active pipeline mode via startup logs and per-email processing logs. Invalid configuration values are caught, logged, and gracefully handled.

**Independent Test**: Start the system in full mode, check logs for mode confirmation. Change to triage-only, restart, verify logs reflect the new mode. Set an invalid value, verify error log and fallback to full.

### Implementation for User Story 3

- [X] T011 [US3] Add structured startup log messages using logging module: log active pipeline mode, triage queue name, and external namespace (if configured) after env var validation in src/agents/run_agent.py — per FR-008 and CAR-008
- [X] T012 [US3] Add per-email pipeline mode log entry at the branch point in process_next_email() — log which mode is being applied for the current email using logging.info() in src/agents/email_classifier_agent.py

**Checkpoint**: Operators can verify configuration via logs. Invalid values fall back gracefully.

---

## Phase 6: User Story 4 — Pipeline State Visibility on Dashboard (Priority: P3)

**Goal**: Dashboard displays current pipeline mode and shows which steps were executed for each email. Operators can verify configuration is taking effect and diagnose processing issues.

**Independent Test**: Configure triage-only mode, process an email, view the dashboard. Verify the mode badge shows "Triage Only" and the email's classification column shows "Skipped (triage-only)".

### Implementation for User Story 4

- [X] T013 [P] [US4] Add pipeline_mode to template context via os.environ.get("PIPELINE_MODE", "full") in the dashboard route handler (GET /) in src/webapp/main.py — per contracts §5
- [X] T014 [US4] Add pipeline mode badge to dashboard header ("Full Pipeline" or "Triage Only") and conditional "Skipped (triage-only)" label in classification column for emails where stepsExecuted lacks "classification" in src/webapp/templates/dashboard.html — treat emails without pipelineMode field as "full" for backward compatibility

**Checkpoint**: Dashboard shows pipeline mode and per-email step visibility. Backward compatible with existing email documents.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cleanup

- [X] T015 Run quickstart.md manual test scenarios: Scenario 1 (triage-only stops after Step 2) and Scenario 2 (full mode unchanged)
- [X] T016 Verify backward compatibility — existing Cosmos DB emails without pipelineMode and stepsExecuted fields display correctly on dashboard as "full" mode
- [X] T017 Verify responsive behavior — confirm pipeline mode badge renders correctly on narrow viewports per CAR-003

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — **BLOCKS all user stories**
- **User Story 1 (Phase 3)**: Depends on Foundational (Phase 2)
- **User Story 2 (Phase 4)**: Depends on Foundational (Phase 2); may run in parallel with US1 if different developers
- **User Story 3 (Phase 5)**: Depends on Foundational (Phase 2); can run in parallel with US1/US2
- **User Story 4 (Phase 6)**: Depends on Foundational (Phase 2) and benefits from US1/US2 being complete (for test data)
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: After Phase 2 — no dependencies on other stories
- **User Story 2 (P1)**: After Phase 2 — no dependencies on other stories (US1 and US2 touch different branches of same conditional, but are independently testable)
- **User Story 3 (P2)**: After Phase 2 — logging tasks are independent of mode-specific logic
- **User Story 4 (P3)**: After Phase 2 — dashboard changes are independent but benefit from processed emails for visual verification

### Within Each User Story

- Implementation before tests (in this case, the branch logic must exist before tests can verify it)
- Core implementation before error handling
- Story complete before moving to next priority

### Parallel Opportunities

- T003 and T004 can run in parallel (different files: queue_tools.py vs cosmos_tools.py)
- T013 can run in parallel with T014 (different files: main.py vs dashboard.html)
- US1 and US2 can proceed in parallel after Phase 2 (different branches of same conditional, different queue methods)
- US3 (logging) can run in parallel with US1/US2 (touches different sections of the same files)

---

## Parallel Example: Phase 2 (Foundational)

```
# These can run simultaneously (different files):
T003: Extend QueueTools with triage params in src/agents/tools/queue_tools.py
T004: Add Cosmos DB fields in src/agents/tools/cosmos_tools.py
```

## Parallel Example: User Stories After Phase 2

```
# These can proceed in parallel (different branches/methods):
Developer A → US1: Full-mode branch + test (T005, T006, T007)
Developer B → US2: Triage-only branch + test (T008, T009, T010)
Developer C → US3: Logging (T011, T012)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002, T003, T004)
3. Complete Phase 3: User Story 1 (T005, T006, T007)
4. **STOP and VALIDATE**: Full-pipeline mode works, pipelineMode recorded, backward compatible
5. Deploy/demo if ready — existing behavior preserved with pipeline mode tracking

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add User Story 1 → Full pipeline works with recording → **MVP!**
3. Add User Story 2 → Triage-only mode operational → Core feature complete
4. Add User Story 3 → Operational logging → Operator confidence
5. Add User Story 4 → Dashboard visibility → Full feature delivered
6. Polish → Validation and cleanup

---

## Notes

- [P] tasks = different files, no dependencies on concurrent tasks
- [Story] label maps task to specific user story for traceability
- Tests follow existing patterns: class-per-feature, MagicMock, patch.dict("os.environ"), @pytest.mark.asyncio
- No new dependencies — all SDKs already in requirements.txt
- No new modules or directories — changes contained within existing files + one new test file
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
