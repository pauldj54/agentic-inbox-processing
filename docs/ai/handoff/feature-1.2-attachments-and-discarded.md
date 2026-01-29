# Feature 1.2: Attachment Count Display & Discarded Email Status

## Goal & Current Status

**Goal**: Fix attachment count display in dashboard (always showing 0), ensure discarded emails update status correctly, and add Logic App source control.

**Status: COMPLETE** - Attachment counts now display correctly for new emails. Discarded (non-PE) emails properly transition from `received` → `discarded` status. Logic App workflow reordered so Cosmos DB insert happens after attachment processing.

## Decisions & Constraints

- **String boolean issue**: Logic App sends `hasAttachments` as string `"True"`/`"False"` → added `parse_bool()` helper everywhere
- **Logic App execution order**: Cosmos DB insert was happening BEFORE foreach loop → attachmentsCount was always 0. Fixed by reordering: `Initialize → ForEach → Cosmos Insert → Service Bus`
- **Preserve existing values**: When agent updates document, only overwrite `attachmentsCount` if email_data has a non-zero value; otherwise preserve what Logic App stored
- **Discarded status**: Added second `update_email_classification()` call with `step="final"` when routing to discarded queue
- **Inline attachments excluded**: Only non-inline attachments (real files, not signature images) are counted via `isInline === false` check

## Architecture Snapshot

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Outlook Email                                                               │
│       ↓                                                                      │
│  Logic App (reordered):                                                      │
│    1. Initialize AttachmentsCount=0, AttachmentPaths=[]                     │
│    2. ForEach attachment (if !isInline): upload blob, increment count       │
│    3. Cosmos DB upsert (now has correct count!)                             │
│    4. Service Bus message                                                    │
│       ↓                                                                      │
│  Agent (run_agent.py):                                                       │
│    - Relevance check                                                         │
│    - If NOT relevant → route to discarded + update status to "discarded"   │
│    - If relevant → classify → update status to "classified"/"needs_review" │
│       ↓                                                                      │
│  Dashboard (main.py + dashboard.html):                                       │
│    - parse_bool filter for hasAttachments                                   │
│    - Display attachmentsCount next to ✅ icon                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Files Touched

| Path | Changes |
|------|---------|
| `src/agents/tools/cosmos_tools.py` | Added `parse_bool()` helper; preserve existing `attachmentsCount` when updating documents |
| `src/agents/tools/queue_tools.py` | Added `parse_bool()` helper; normalize `attachmentsCount` to int in message parsing |
| `src/agents/email_classifier_agent.py` | Added `update_email_classification()` call with `step="final"` for discarded emails |
| `src/webapp/main.py` | Added `parse_bool` template filter |
| `src/webapp/templates/dashboard.html` | Use `\|parse_bool` filter; display `attachmentsCount` next to checkbox |
| `logic-apps/email-ingestion/workflow.json` | **NEW** - Logic App workflow with corrected order |
| `logic-apps/email-ingestion/parameters.dev.json` | **NEW** - Connection parameters for dev |
| `logic-apps/email-ingestion/README.md` | **NEW** - Documentation for Logic App |
| `README.md` | Added PE Event Deduplication Criteria section |

## Commands Run

```powershell
# Dashboard testing
python -m uvicorn src.webapp.main:app --reload --port 8000

# Cosmos DB queries to verify data
.\.venv\Scripts\python.exe -c "...query attachmentsCount..."
```

## Known Issues / Next Steps

1. **Existing emails have stale data** - Old emails still have string `hasAttachments` and missing `attachmentsCount`. Will self-heal when reprocessed, or run migration script.
2. **PE event stats query fails** - Dashboard shows "Error getting PE event stats" due to unsupported GROUP BY. Not blocking but should fix.
3. **Orphaned RECEIVED emails** - Some emails stuck in `received` status from previous AI Foundry timeouts. Need cleanup or retry mechanism.

## Acceptance Criteria Checklist

- [x] New emails with attachments show correct count in dashboard (e.g., `✅ 4`)
- [x] Emails without attachments show `—` (dash) in attachment column
- [x] Non-PE emails transition from `received` → `discarded` status
- [x] Inline images (email signatures) not counted as attachments
- [x] Logic App workflow stored in source control (`logic-apps/`)
- [x] `hasAttachments` handled as boolean regardless of string/bool input
- [x] Existing `attachmentsCount` preserved when agent updates classification
