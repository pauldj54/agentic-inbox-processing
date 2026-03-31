# Implementation Plan: Attachment Delivery Tracking for Email and Download Links

**Branch**: `006-attachment-delivery-tracking` | **Date**: 2026-03-30 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/006-attachment-delivery-tracking/spec.md`

## Summary

Extend the version and delivery badge tracking — currently implemented only for SFTP intake records — to also cover email attachments and direct download links. The email Logic App extracts Content-MD5 from blob uploads via HTTP HEAD, performs partition-scoped content-hash dedup (3-way routing: new / duplicate / update), and populates delivery tracking fields on Cosmos DB records. The Python link download tool computes MD5 from in-memory bytes and uses the same `find_by_content_hash()` helper. The dashboard badge guard changes from `intakeSource == 'sftp'` to `version is defined` so badges render for all intake channels.

## Technical Context

**Language/Version**: Python 3.12+, Azure Logic Apps (Consumption tier, workflow definition JSON)
**Primary Dependencies**: azure-cosmos (existing), azure-storage-blob (existing), azure-identity (existing), FastAPI + Jinja2 (existing webapp)
**Storage**: Azure Cosmos DB (`intake-records` container, `/partitionKey` partition key). Azure Blob Storage (`attachments` container).
**Testing**: pytest — unit tests for `find_by_content_hash()`, content hash computation, and delivery tracking flows. Manual E2E via quickstart.md.
**Target Platform**: Azure App Service (Python webapp), Azure Logic App (email ingestion), Azure Logic App (SFTP ingestion — unchanged)
**Project Type**: Multi-component cloud workflow (Logic App + Python backend + dashboard)
**Performance Goals**: Dashboard badge rendering adds <100ms to page load (SC-005). Email processing within 1 Logic App run cycle (~1 min).
**Constraints**: No new dependencies. Dedup scoped per Cosmos DB partition (sender domain + year-month). No cross-partition or cross-channel dedup.
**Scale/Scope**: ~10-50 emails/day, ~hundreds of records per partition. Partition-scoped queries are efficient.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **Code simplicity gate**: Reuses the same 3-way routing pattern (new/duplicate/update) and field schema already implemented for SFTP. No new abstractions. `find_by_content_hash()` is ~15 LOC. Logic App changes add conditional routing mirroring the existing SFTP dedup pattern.
- [x] **UX gate**: Single change — badge guard condition. No new UI components, screens, or interactions. Operators see the same v1/2x badges they already see for SFTP, now extended to email and link-sourced records.
- [x] **Responsive gate**: The version/delivery badge column already exists in the dashboard table and is responsive. No layout changes required.
- [x] **Dependency gate**: Zero new dependencies. Content-MD5 comes from Azure Blob Storage HTTP HEAD (Logic App) or `hashlib.md5()` (Python stdlib). All Azure SDKs already in requirements.txt.
- [x] **Auth gate**: Logic App uses ManagedServiceIdentity for blob HEAD requests. Python uses DefaultAzureCredential for Cosmos DB and Blob Storage. No auth changes.
- [x] **Validation gate**: 7 focused unit tests covering: find_by_content_hash (match/no-match/null), link download delivery tracking (new/duplicate/failed), content update version increment. Manual E2E via quickstart.md (5 verification tests). Proportional to scope.
- [x] **Logging gate**: Dedup decisions logged with structured fields (content hash, action, matched record ID) via `logger.info()` in cosmos_tools.py. No print statements.

## Project Structure

### Documentation (this feature)

```text
specs/006-attachment-delivery-tracking/
├── plan.md              # This file
├── research.md          # Phase 0 output — 6 research findings
├── data-model.md        # Phase 1 output — extended intake record schema
├── quickstart.md        # Phase 1 output — 5 manual verification tests
├── contracts/
│   └── contracts.md     # Phase 1 output — 5 interface contracts
└── tasks.md             # Phase 2 output — 26 tasks across 7 phases
```

### Source Code (repository root)

```text
logic-apps/
└── email-ingestion/
    └── workflow.json        # MODIFY: add Get_attachment_md5 HEAD action, Compute_primary_hash,
                             #   dedup query, 3-way conditional routing, delivery tracking fields

src/
├── agents/
│   └── tools/
│       ├── cosmos_tools.py      # MODIFY: add find_by_content_hash()
│       └── link_download_tool.py # MODIFY: add content_md5 field, MD5 computation from bytes
└── webapp/
    └── templates/
        └── dashboard.html       # MODIFY: change badge guard from intakeSource=='sftp' to version-based

tests/
└── unit/
    └── test_delivery_tracking.py # NEW: 7 unit tests for delivery tracking helpers
```

**Structure Decision**: Follows established project conventions. Logic App workflow changes are in the existing `logic-apps/email-ingestion/workflow.json`. Python helpers are added to the existing `cosmos_tools.py` (which already handles all Cosmos DB operations). Link download tool extension in existing `link_download_tool.py`. Dashboard guard change in existing template. New test file in `tests/unit/`.

## Complexity Tracking

No constitution violations identified. All gates pass.

| Note | Detail |
|------|--------|
| Logic App HEAD request | Same pattern as SFTP `Get_blob_md5`. Uses ManagedServiceIdentity for blob header access. |
| Dedup query vs point-read | Email records use `messageId` as document ID, so content dedup requires partition-scoped SQL query (unlike SFTP's path-based point-read). Efficient for partition sizes of ~hundreds of records. |
| Race condition acceptance | Concurrent dedup race window is negligible (~seconds between Logic App runs). Rare duplicates caught on next delivery. No concurrency controls added. |

## Post-Design Constitution Re-evaluation

*Re-checked after Phase 1 design completion. All gates remain PASS.*

- Code simplicity: Confirmed — `find_by_content_hash()` is a small, focused function. Logic App dedup follows the proven SFTP pattern. No new patterns introduced.
- UX: Confirmed — badge guard is a single-line conditional change. No new components.
- Dependencies: Confirmed — zero new packages. MD5 computed via stdlib `hashlib`.
- Auth: Confirmed — ManagedServiceIdentity for blob HEAD, DefaultAzureCredential for Cosmos DB.
- Testing: Confirmed — 7 unit tests cover all helper function and delivery tracking paths. Manual E2E quickstart covers the full flow.
- Logging: Confirmed — structured `logger.info()` for dedup decisions with hash, action, record ID.
