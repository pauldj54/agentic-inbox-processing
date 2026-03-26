# Implementation Plan: Triage Consumer Client

**Branch**: `005-triage-consumer` | **Date**: 2025-07-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/005-triage-consumer/spec.md`

## Summary

Build a Python CLI client that continuously listens to the Azure Service Bus `triage-complete` queue, displays formatted document information in the terminal, and forwards each triaged document to a configurable external document processing API. Includes a companion test utility for sending realistic sample messages to the queue without depending on the upstream pipeline.

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: azure-servicebus (>=7.12.0, existing), azure-identity (>=1.15.0, existing), requests (new — HTTP client for API calls), python-dotenv (existing)
**Storage**: N/A — reads from Azure Service Bus queue, posts to external HTTP API. No local or cloud persistence.
**Testing**: pytest for unit tests on message parsing and API request building; manual test utility (`send_test_triage_message.py`) for end-to-end verification
**Target Platform**: Local development machine (Windows/macOS/Linux CLI). Not deployed to Azure App Service.
**Project Type**: CLI tool (development/operations utility)
**Performance Goals**: Process messages within 30 seconds of queue arrival (SC-001). No throughput targets — low-volume operational tool.
**Constraints**: Single-consumer, single-threaded. Max 30s timeout per API call. Graceful shutdown on Ctrl+C.
**Scale/Scope**: Single queue, single consumer instance, low message volume (~tens/day)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **Code simplicity gate**: Consumer is a single-file script with flat functions (no classes, no inheritance, no plugin architecture). Test utility is a separate single file. Total ~350 LOC across both files.
- [x] **UX gate**: Run script → see output → Ctrl+C to stop. No interactive menus, no configuration wizards, no prompts during runtime.
- [x] **Responsive gate**: N/A — terminal-only tool, no UI.
- [x] **Dependency gate**: One new dependency: `requests`. Justified because `aiohttp` (existing in project) is async and would add unnecessary complexity for a simple synchronous POST call. `requests` is the de facto standard Python HTTP client. All Azure SDKs are already in the project.
- [x] **Auth gate**: Service Bus access uses `DefaultAzureCredential` (Entra ID via managed identity or local az login). No shared keys or connection strings. External API call uses no auth by default (configurable by operator).
- [x] **Validation gate**: Unit tests cover message parsing and API request building (the two transform functions). The test utility provides manual end-to-end verification. No integration test against live Service Bus needed (low-risk tool, not production service).
- [x] **Logging gate**: Operational events (connect, receive, process, API call, errors) use Python `logging` module with severity levels. Formatted `print` output is intentional for the terminal display feature (the core purpose of the tool) and clearly separated from operational logs.

## Project Structure

### Documentation (this feature)

```text
specs/005-triage-consumer/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
src/
└── triage_consumer.py      # NEW: main consumer — queue listener, message display, API forwarding

utils/
└── send_test_triage_message.py  # NEW: test utility — send sample messages to queue

tests/
└── unit/
    └── test_triage_consumer.py  # NEW: unit tests for message parsing and API request building

requirements.txt                 # MODIFY: add requests dependency
TRIAGE_CONSUMER.md               # NEW: usage documentation (at repo root, since docs/ is gitignored)
```

**Structure Decision**: Follows existing project conventions. Consumer script in `src/` alongside `peek_queue.py` (similar queue-reading tool). Test utility in `utils/` alongside similar queue diagnostics (`purge_queues.py`, `send_test_email.py`). Unit tests in established `tests/unit/` directory.

## Complexity Tracking

No constitution violations identified. All gates pass.

| Note | Detail |
|------|--------|
| `requests` dependency | Not yet in requirements.txt — must be added. Justified over `aiohttp` for synchronous simplicity. |
| `print` vs `logging` | Formatted `print` output is the feature, not a logging concern. Clearly scoped to `print_message_details()` function only. |

## Post-Design Constitution Re-evaluation

*Re-checked after Phase 1 design completion. All gates remain PASS.*

No new violations surfaced from research or design artifacts. Key confirmations:
- Consumer remains a single-file script (~200 LOC) with flat functions — no class hierarchies emerged from data model design
- `requests` is the only new dependency (justified in research R-003)
- Data model documents two flat entities; no ORM, no schema migrations, no new storage
- CLI contract confirms zero interactive prompts in the consumer (UX simplicity)
- `print` output is intentional for the display feature (the core P1 user story) and clearly separated from `logging` calls

## Implementation Phasing

### Phase A: Consumer Core (P1 — queue listening + display)

1. Create `src/triage_consumer.py` with environment variable loading and Service Bus connection
2. Implement `print_message_details()` for formatted terminal output (email + SFTP message formats)
3. Implement `run_consumer_loop()` with continuous receive-process-complete cycle
4. Add graceful shutdown on KeyboardInterrupt

### Phase B: API Forwarding (P2 — transform + POST)

1. Implement `build_api_request()` to transform triage message → API payload per data model
2. Implement `call_api()` with requests.post, timeout, and error handling
3. Wire into `process_message()` flow: parse → display → API call → complete

### Phase C: Test Utility + Housekeeping (P3)

1. Create `utils/send_test_triage_message.py` with realistic email and SFTP sample messages
2. Create documentation (`TRIAGE_CONSUMER.md`)
3. Add unit tests for `build_api_request()` and message parsing edge cases

*Note: `requests>=2.31.0` addition to `requirements.txt` is in Phase A Setup (task T002), since it's needed before Phase B API forwarding.*
