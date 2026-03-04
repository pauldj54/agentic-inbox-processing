# Implementation Plan: Pipeline Configuration

**Branch**: `002-pipeline-config` | **Date**: 2026-03-04 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-pipeline-config/spec.md`

## Summary

Add a configurable pipeline mode (`PIPELINE_MODE` env var) to the email processing agent, enabling two operating modes: **full-pipeline** (existing: triage → classification → routing) and **triage-only** (triage → pre-processing → route to configurable output queue, skipping classification). The triage-only output queue serves as an integration point with an external IDP system — both the queue name (`TRIAGE_COMPLETE_QUEUE`) and Service Bus namespace (`TRIAGE_COMPLETE_SB_NAMESPACE`) are configurable to allow routing to a separate IDP-owned namespace. Implementation is a simple conditional branch in `email_classifier_agent.py`, a new env-var block in `run_agent.py`, a dedicated `ServiceBusClient` for the external namespace in `queue_tools.py`, minor Cosmos DB schema extension, and minimal dashboard changes.

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: FastAPI, azure-identity, azure-cosmos, azure-servicebus, azure-ai-agents, azure-ai-documentintelligence, azure-storage-blob, aiohttp, jinja2, python-dotenv  
**Storage**: Azure Cosmos DB (`email-processing` database, `emails` container), Azure Blob Storage  
**Testing**: pytest with asyncio_mode="auto"  
**Target Platform**: Azure App Service (Linux) for web dashboard + Python agent, Azure Logic Apps for email trigger  
**Project Type**: Agentic pipeline + web dashboard  
**Performance Goals**: Triage-only mode should be faster than full-pipeline (classification step eliminated)  
**Constraints**: No new dependencies. Backward compatible — unset `PIPELINE_MODE` defaults to `full`.  
**Scale/Scope**: Single-inbox monitoring, low-to-moderate email volume (~tens/day)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **Code simplicity gate**: Pipeline mode is a single if/else branch in `process_next_email()` at the classification step. No strategy pattern, no plugin architecture. Three new env vars (`PIPELINE_MODE`, `TRIAGE_COMPLETE_QUEUE`, `TRIAGE_COMPLETE_SB_NAMESPACE`) follow existing patterns. External namespace connection is an optional secondary `ServiceBusClient` in `QueueTools`.
- [x] **UX gate**: Dashboard change is minimal — a mode indicator label in the header area and a "skipped (triage-only mode)" label on the classification step for triage-only emails. No new screens or flows.
- [x] **Responsive gate**: Mode indicator is inline text that flows naturally on all viewports. Existing responsive layout preserved.
- [x] **Dependency gate**: No new dependencies. All required SDKs (`azure-servicebus`, `azure-identity`, `python-dotenv`) are already in `requirements.txt`.
- [x] **Auth gate**: External Service Bus namespace uses `DefaultAzureCredential` (Entra ID), consistent with all other service connections. No shared keys or secrets.
- [x] **Validation gate**: Two core tests per CAR-007: (1) triage-only mode skips classification and routes to triage-complete, (2) default/full mode preserves existing behaviour. Proportional and focused.
- [x] **Logging gate**: Pipeline mode logged at startup via `logging` module. Each email processing logs which mode was applied. No print statements.

## Project Structure

### Documentation (this feature)

```text
specs/002-pipeline-config/
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
├── agents/
│   ├── email_classifier_agent.py   # MODIFY: add pipeline mode branch (skip classification in triage-only)
│   ├── run_agent.py                # MODIFY: load + validate PIPELINE_MODE, TRIAGE_COMPLETE_QUEUE, TRIAGE_COMPLETE_SB_NAMESPACE env vars
│   ├── classification_prompts.py   # NO CHANGE
│   └── tools/
│       ├── queue_tools.py          # MODIFY: add triage-complete routing method, optional external SB client
│       ├── cosmos_tools.py         # MODIFY: add pipelineMode + stepsExecuted fields to email record updates
│       ├── document_intelligence_tool.py  # NO CHANGE
│       ├── graph_tools.py          # NO CHANGE
│       └── link_download_tool.py   # NO CHANGE
├── webapp/
│   ├── main.py                     # MODIFY: expose pipeline mode in dashboard context
│   └── templates/
│       └── dashboard.html          # MODIFY: add mode indicator + "skipped" label on classification step

tests/
├── unit/
│   └── test_pipeline_config.py     # NEW: 2 core tests (triage-only routing, full-mode preserved)
```

**Structure Decision**: Follows existing flat-tools pattern. No new modules — changes are contained within existing files. One new test file. No new directories needed.

## Post-Design Constitution Re-evaluation

*After Phase 1 design, all gates re-checked against concrete artifacts.*

- [x] **Code simplicity gate**: Design confirmed — single if/else branch in `process_next_email()` at line ~353. No new abstractions, patterns, or indirection layers. `send_to_triage_queue()` follows existing `_send_to_queue()` pattern. Two new optional Cosmos fields added inline. ✅ PASS
- [x] **UX gate**: Dashboard changes confirmed minimal — one context variable (`pipeline_mode`), one badge, one conditional label. No new routes, pages, or interactive controls. ✅ PASS
- [x] **Responsive gate**: Badge is inline text in existing header layout. No layout impact. ✅ PASS
- [x] **Dependency gate**: Confirmed zero new dependencies. All SDK usage (`azure-servicebus`, `azure-identity`) already present. ✅ PASS
- [x] **Auth gate**: External SB namespace uses `DefaultAzureCredential` — same as all internal connections. Documented in contracts. ✅ PASS
- [x] **Validation gate**: Two tests confirmed in scope — triage-only skip and full-mode preservation. Test patterns match existing `test_link_download_tool.py` style. ✅ PASS
- [x] **Logging gate**: Pipeline mode logged at startup and per-email. Structured logging via `logging` module. No print statements. ✅ PASS

**Result**: All 7 gates PASS. No violations. No exceptions required.

## Artifacts Generated

| Artifact | Path | Status |
|---|---|---|
| Implementation Plan | `specs/002-pipeline-config/plan.md` | ✅ Complete |
| Research | `specs/002-pipeline-config/research.md` | ✅ Complete |
| Data Model | `specs/002-pipeline-config/data-model.md` | ✅ Complete |
| Contracts | `specs/002-pipeline-config/contracts/contracts.md` | ✅ Complete |
| Quickstart | `specs/002-pipeline-config/quickstart.md` | ✅ Complete |
| Tasks | `specs/002-pipeline-config/tasks.md` | ✅ Complete |

## Complexity Tracking

No constitution violations identified. All gates pass pre- and post-design.
