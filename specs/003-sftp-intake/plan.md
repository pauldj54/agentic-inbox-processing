# Implementation Plan: SFTP File Intake — Content Hash Dedup & Delivery Tracking

**Branch**: `003-sftp-intake` | **Date**: 2026-03-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-sftp-intake/spec.md`

**Note**: This plan covers the **content hash dedup, file update detection, and delivery tracking** enhancements to the existing SFTP intake Logic App workflow. The base workflow (trigger, download, parse, backup, route, archive) is already deployed and working.

## Summary

Enhance the SFTP intake workflow with content-hash-based duplicate detection, file update handling, and delivery tracking. The current dedup key (`base64(path)`) only catches same-path duplicates but cannot distinguish re-uploads of identical files from genuine content updates. By leveraging the MD5 hash that Azure Blob Storage automatically computes on upload, the system gains content-aware dedup at zero additional cost. The Cosmos DB data model is extended with `contentHash`, `version`, `deliveryCount`, and `deliveryHistory` fields, and the partition key is changed from `/status` to `/partitionKey` — a composite value combining source identifier with year-month (e.g., `partnerreader_2026-03` for SFTP, `partner-pe-firm.com_2026-03` for email) — to enable tenant-per-month data distribution and stable point-reads within each partition.

## Technical Context

**Language/Version**: Logic Apps (Consumption tier, Azure), Python 3.12 (agent/dashboard)
**Primary Dependencies**: Logic App managed connectors (SFTP-SSH, Cosmos DB, Blob, Service Bus), Graph API (SharePoint), Python Azure SDKs
**Storage**: Cosmos DB (`email-processing` db, `intake-records` collection), Azure Blob Storage (`stdocprocdevizr2ch55`), SFTP (`sftpprocdevizr2ch55`)
**Testing**: Manual Logic App runs + pytest (unit/integration)
**Target Platform**: Azure (Logic Apps Consumption, swedencentral)
**Project Type**: Cloud workflow (Logic Apps) + web-service (Python dashboard)
**Performance Goals**: File processing < 2 minutes from SFTP trigger to archive (SC-001)
**Constraints**: Must be SFTP-agnostic (no reliance on Azure-specific SFTP features for content hashing). Consumption tier Logic App limitations (no stateful workflows, no Standard-tier inline code).
**Scale/Scope**: Single Logic App workflow, ~15 actions, single Cosmos DB collection

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Code simplicity gate**: PASS — Changes are confined to reordering existing Logic App actions and adding fields to the Cosmos DB document. No new abstractions or services introduced. Content hash uses the MD5 already computed by Blob Storage.
- **UX gate**: PASS — Dashboard gains a delivery count badge and version indicator on existing records. No new screens or flows.
- **Responsive gate**: PASS — Dashboard changes are column additions to existing table; responsive behavior already handled.
- **Dependency gate**: PASS — No new packages. Blob Storage MD5 is a built-in feature. Cosmos DB partition key change uses existing SDK.
- **Auth gate**: PASS — No auth changes. All connections continue using existing auth (MI for Cosmos/Blob/SB, SSH key for SFTP, OAuth for SharePoint). Constitution exception for SharePoint client secret is documented in CAR-005.
- **Validation gate**: PASS — Tests cover 3 scenarios: new file, true duplicate, content update. Proportional to the 3-way routing logic.
- **Logging gate**: PASS — Dedup outcomes (new/duplicate/update) are tracked in Cosmos DB `deliveryHistory` — structured and queryable. No print-style logging.

## Project Structure

### Documentation (this feature)

```text
specs/003-sftp-intake/
├── plan.md              # This file (updated for content hash dedup)
├── research.md          # Phase 0 output (updated)
├── data-model.md        # Phase 1 output (updated with new fields + partition key change)
├── quickstart.md        # Phase 1 output (updated)
├── contracts/           # Phase 1 output (updated)
│   └── contracts.md
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
logic-apps/
└── sftp-file-ingestion/
    └── workflow.json         # Logic App workflow definition (reordered + new actions)

infrastructure/
├── main.bicep               # Updated: new Cosmos container or migration script
└── modules/
    └── cosmos-db.bicep       # Updated: partition key change

src/
└── webapp/
    └── templates/
        └── dashboard.html    # Updated: delivery count + version columns

specs/
└── 003-sftp-intake/
    ├── data-model.md         # Updated: new fields, partition key
    └── contracts/
        └── contracts.md      # Updated: workflow action order, dedup logic
```

**Structure Decision**: Logic App workflow + Cosmos DB schema changes + dashboard columns. No new services or compute resources.

## Complexity Tracking

> **No constitution violations identified.** All gates pass. The design uses existing blob MD5 (no new dependencies), keeps the Logic App simple (reorder + add patch actions), and the partition key change is the minimum viable fix for stable dedup reads.
