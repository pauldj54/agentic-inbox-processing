# Implementation Plan: SFTP File Disposition (Success/Failure Routing)

**Branch**: `004-sftp-file-disposition` | **Date**: 2026-03-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-sftp-file-disposition/spec.md`

## Summary

Replace the current delete-only behavior for SFTP files with outcome-based disposition. Successfully processed files are moved from `/in/` to `/processed/`, failed files are moved from `/in/` to `/failed/`, and true duplicate files are moved to `/processed/`. Unsupported file types are deleted from `/in/` (matching existing behavior). The implementation introduces two Scopes — `Scope_Early_Processing` (wrapping `Get_file_content` through `Compute_dedup_key` for early failure coverage) and `Scope_Route_File` (wrapping `Create_intake_record_if_new` + `Check_if_spreadsheet` for downstream failure coverage) — adds SFTP copy + delete disposition actions at each terminal path, and extends Cosmos DB records with `disposition` and `errorDetails` fields. The SFTP trigger uses a watermark model that advances past each file regardless of run success, so ALL failures must route to `/failed/` (files left in `/in/` are never re-triggered). No new dependencies, services, or compute resources are introduced.

## Technical Context

**Language/Version**: Logic Apps (Consumption tier, Azure) — workflow JSON definition
**Primary Dependencies**: Logic App managed connectors (SFTP-SSH for copy/delete, Cosmos DB for record updates)
**Storage**: Cosmos DB (`email-processing` db, `intake-records` container), SFTP server (`sftpprocdevizr2ch55`)
**Testing**: Manual Logic App runs (place files in SFTP `/in/`, verify folder outcomes + Cosmos DB records)
**Target Platform**: Azure (Logic Apps Consumption, swedencentral)
**Project Type**: Cloud workflow (Logic Apps Consumption tier)
**Performance Goals**: File disposition (copy + delete) < 30 seconds added to existing processing time
**Constraints**: Consumption tier Logic App limitations (no stateful workflows). SFTP rename is broken on HNS storage — must use copy + delete pattern. File delete must use file ID (not path) due to UTF-8 special character issues.
**Scale/Scope**: Single Logic App workflow modification, ~14 new actions, +2 Scopes, 2 removed actions, 1 new parameter, 2 new Cosmos DB fields

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Code simplicity gate**: PASS — Changes are confined to the existing Logic App workflow. New actions follow established patterns (copy, delete, Cosmos upsert). Two Scopes group early processing (`Scope_Early_Processing`) and downstream processing (`Scope_Route_File`) for consolidated error handling. `Handle_duplicate_check` sits between the two Scopes because Terminate inside a Scope kills the entire run. No new services or abstractions.
- **UX gate**: PASS — No UI changes. Operators inspect SFTP folders directly. The `disposition` field enables future dashboard filtering but no dashboard changes in this feature.
- **Responsive gate**: PASS — No UI changes.
- **Dependency gate**: PASS — No new dependencies. Uses existing SFTP-SSH connector (already has copy/delete capabilities) and existing Cosmos DB connector.
- **Auth gate**: PASS — No authentication changes. Existing SFTP SSH-key auth and Cosmos DB managed identity continue unchanged.
- **Validation gate**: PASS — Tests cover 5 scenarios: (1) success → `/processed/`, (2) downstream failure → `/failed/`, (3) duplicate → `/processed/`, (4) unsupported → deleted from `/in/`, (5) early failure (e.g., blob upload) → `/failed/`. Proportional to the branching logic.
- **Logging gate**: PASS — Disposition outcomes are tracked in Cosmos DB (`disposition` field) and Logic App run history. No print-style logging.

### Post-Phase 1 Re-check

- **Code simplicity**: CONFIRMED — Two Scopes group early processing and downstream processing. ~14 new actions follow copy/delete/upsert patterns already established in the workflow. `Handle_duplicate_check` sits between Scopes to avoid Terminate-inside-Scope issues.
- **Dependency**: CONFIRMED — Zero new packages or services.
- **Validation**: CONFIRMED — 5 test scenarios match the 5 disposition paths (success, downstream failure, duplicate, unsupported, early failure).

## Project Structure

### Documentation (this feature)

```text
specs/004-sftp-file-disposition/
├── plan.md              # This file
├── research.md          # Phase 0 output — error handling pattern decisions
├── data-model.md        # Phase 1 output — disposition + errorDetails fields
├── quickstart.md        # Phase 1 output — testing guide
├── contracts/           # Phase 1 output — updated workflow action sequence
│   └── contracts.md
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
logic-apps/
└── sftp-file-ingestion/
    ├── workflow.json         # Modified: Scope, disposition actions, new parameter
    └── README.md             # Updated: action table, error handling section

logic-apps/
└── sftp-file-ingestion/
    └── parameters.dev.json   # Updated: add sftpFailedPath parameter
```

**Structure Decision**: Single Logic App workflow modification. No new files or services. All changes are to `workflow.json` (action definitions + parameter), `parameters.dev.json` (parameter value), and `README.md` (documentation).

## Complexity Tracking

> **No constitution violations identified.** All gates pass. The design uses two Logic App Scopes (`Scope_Early_Processing` + `Scope_Route_File`) for comprehensive error handling, reuses existing copy + delete patterns, and adds two flat Cosmos DB fields.
