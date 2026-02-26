# Implementation Plan: Download-Link Intake

**Branch**: `001-download-link-intake` | **Date**: 2026-02-26 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-download-link-intake/spec.md`

## Summary

Enrich the email intake pipeline to detect download links in email bodies, fetch the linked documents, and store them in Azure Blob Storage alongside traditional attachments. A new Python pre-processing module runs after Service Bus message receipt and before classification, scanning the email body for document URLs, downloading files via HTTPS, uploading to Blob Storage using the Azure SDK, and updating the Cosmos DB record and Service Bus-sourced email data with the enriched attachment metadata. The `attachmentPaths` field migrates from a flat string array to an array of objects (`{path, source}`) across Logic App, Cosmos DB, and all consumers.

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: FastAPI, azure-identity, azure-cosmos, azure-servicebus, azure-ai-agents, azure-ai-documentintelligence, azure-storage-blob (new), aiohttp, jinja2  
**Storage**: Azure Cosmos DB (`email-processing` database, `emails` container), Azure Blob Storage (`stdocprocdevizr2ch55`, `/attachments/{emailId}/{filename}`)  
**Testing**: pytest (to be introduced — no test infrastructure exists yet)  
**Target Platform**: Azure App Service (Linux) for web dashboard + Python agent, Azure Logic Apps for email trigger  
**Project Type**: Agentic pipeline + web dashboard  
**Performance Goals**: < 30s additional latency per email for link download (SC-001)  
**Constraints**: Max file size per download: 50 MB (configurable). Download timeout: 30s per file. Only public HTTPS links (no auth).  
**Scale/Scope**: Single-inbox monitoring, low-to-moderate email volume (~tens/day)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **Code simplicity gate**: New module `link_download_tool.py` is single-responsibility (detect links, download, upload to blob). URL detection uses simple regex for known document extensions — no over-engineered NLP. Cosmos/Service Bus schema migration is a contained change in two existing files.
- [x] **UX gate**: Dashboard change is minimal — a small icon/label on each attachment entry indicating `"link"` vs `"attachment"` source. No new screens, modals, or controls.
- [x] **Responsive gate**: Attachment source indicator uses inline text/icon that flows naturally on mobile viewports. Existing responsive layout is preserved.
- [x] **Dependency gate**: One new package: `azure-storage-blob` (official Microsoft SDK, required for blob upload from Python). HTTP downloads use existing `aiohttp` already in requirements. No other new dependencies.
- [x] **Auth gate**: Blob Storage access uses `DefaultAzureCredential` (Entra ID). External link downloads use standard unauthenticated HTTPS — documented exception per spec (Assumption #1).
- [x] **Validation gate**: Three focused test areas: (1) link detection regex, (2) download + blob upload happy path, (3) failure handling (timeout, 404, bad content-type). No fuzzing or exhaustive URL parsing tests.
- [x] **Logging gate**: All download attempts, successes, failures, and skips logged via `logging` module with severity levels. No print statements.

## Project Structure

### Documentation (this feature)

```text
specs/001-download-link-intake/
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
│   ├── email_classifier_agent.py   # MODIFY: add link-enrichment pre-processing step, update attachmentPaths handling
│   ├── classification_prompts.py   # NO CHANGE
│   └── tools/
│       ├── cosmos_tools.py         # MODIFY: update attachmentPaths handling to object format
│       ├── document_intelligence_tool.py  # NO CHANGE
│       ├── graph_tools.py          # NO CHANGE
│       ├── queue_tools.py          # REVIEW: may need minor update for object attachmentPaths
│       └── link_download_tool.py   # NEW: link detection, download, blob upload
├── webapp/
│   ├── main.py                     # MODIFY: update attachment display logic for object format
│   └── templates/
│       └── dashboard.html          # MODIFY: add source indicator on attachment entries
└── peek_queue.py                   # NO CHANGE

logic-apps/
└── email-ingestion/
    └── workflow.json               # MODIFY: change attachmentPaths to object format for traditional attachments

tests/
├── unit/
│   └── test_link_download_tool.py  # NEW: link detection regex, filename derivation
└── integration/
    └── test_link_download_flow.py  # NEW: end-to-end download + blob upload + Cosmos update
```

**Structure Decision**: Follows existing flat-tools pattern under `src/agents/tools/`. New `link_download_tool.py` sits alongside existing tools. Test directory is new (project had no tests). Logic App workflow gets a targeted schema update for the `attachmentPaths` field.

## Complexity Tracking

No constitution violations identified. All gates pass.

## Post-Design Constitution Re-evaluation

*Re-checked after Phase 1 design completion. All gates remain PASS.*

No new violations surfaced from research or design artifacts. Key confirmations:
- `link_download_tool.py` remains a single-responsibility module (~150 LOC estimated)
- `azure-storage-blob` is the only new dependency (official SDK, justified)
- Backward-compatible reading of `attachmentPaths` (string or object) ensures smooth transition
- `downloadFailures` is an optional field — does not impact happy path complexity

## Implementation Phasing

### Phase A: Schema Migration (breaking, deploy together)

1. **Logic App `workflow.json`**: Change `Append_to_AttachmentPaths` value to `{"path": "...", "source": "attachment"}`
2. **`email_classifier_agent.py`**: Update all `attachmentPaths` iteration to handle object format (backward-compatible with string format)
3. **`cosmos_tools.py`**: Update attachment-related reads/writes for object format
4. **`queue_tools.py`**: Review and update if it iterates `attachmentPaths`
5. **`webapp/main.py` + `dashboard.html`**: Update attachment display for object format + add source indicator

### Phase B: Link Download Module (new functionality)

1. **`link_download_tool.py`**: New module — URL detection, HTTP download, blob upload
2. **`requirements.txt`**: Add `azure-storage-blob>=12.19.0`
3. **`email_classifier_agent.py`**: Integrate link-download pre-processing step before classification

### Phase C: Testing & Validation

1. **`tests/unit/test_link_download_tool.py`**: URL detection, filename derivation, content-type filtering
2. **`tests/integration/test_link_download_flow.py`**: End-to-end with mocked HTTP + real/mocked blob storage
3. **Manual testing**: Send test emails per quickstart.md scenarios

## Open Design Decisions

| Decision | Recommendation | Status |
|---|---|---|
| Record download failures in Cosmos DB? | Yes — lightweight `downloadFailures` array on email document (see research.md §5) | Plan recommendation (unanswered clarification Q2) |
| HEAD probe for extension-less URLs? | Optional, off by default — enable via config flag for future iteration | Deferred to Phase B implementation |
| Cloud storage link recognition (SharePoint, Dropbox)? | Log recognized patterns but treat as download failures per Assumption #1 | Deferred to future feature |

## Artifacts Generated

| Artifact | Path | Description |
|---|---|---|
| Plan | [plan.md](plan.md) | This implementation plan |
| Research | [research.md](research.md) | Technical research for all unknowns |
| Data Model | [data-model.md](data-model.md) | Entity schemas, migration notes, state transitions |
| Contracts | [contracts/contracts.md](contracts/contracts.md) | Service Bus message, Cosmos DB, tool interface, dashboard API |
| Quickstart | [quickstart.md](quickstart.md) | Setup, testing, and troubleshooting guide |
