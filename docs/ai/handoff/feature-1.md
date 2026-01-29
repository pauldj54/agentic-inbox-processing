# Feature 1: Email Classification Pipeline & Cosmos DB Data Model Fix

## Goal & Current Status

Built an email classification pipeline that receives emails via Logic App → Service Bus → AI Agent → Cosmos DB, with PE event deduplication. Fixed critical bug where changing partition key (`/status`) created duplicate documents instead of updating.

**Status: COMPLETE** - Pipeline processes emails correctly, documents update in place, PE events deduplicate properly.

## Decisions & Constraints

- **Partition key is `/status`** - changing status requires delete-then-upsert (Cosmos limitation)
- **Confidence threshold: 65%** - ≥65% → `archival-pending`, <65% → `human-review`
- **4 queues**: `email-intake`, `discarded`, `human-review`, `archival-pending`
- **Field mapping**: Logic App sends `bodyText`/`sender`, agent expects `emailBody`/`from` - code handles both
- **Two-phase classification**: relevance check (step="relevance") → full classification (step="final")
- **PE deduplication**: Hash of `pe_company|fund_name|category|amount|due_date` stored in `pe-events` container

## Architecture Snapshot

```
Logic App (O365 trigger)
    ↓ JSON message
Service Bus (email-intake queue)
    ↓ receive + complete
EmailClassificationAgent
    ├── relevance check → Cosmos (status=received)
    ├── full classification → Cosmos (status=classified/needs_review/discarded)
    ├── PE event dedup check → pe-events container
    └── route to queue → archival-pending/human-review/discarded
    ↓
Cosmos DB
    ├── emails (partition: /status)
    ├── pe-events (partition: /eventType)
    ├── classifications
    └── audit-logs
```

## Files Touched

| Path | Change |
|------|--------|
| `src/agents/tools/cosmos_tools.py` | Fixed duplicate doc bug: capture `old_status` BEFORE modifying doc; added field name mapping (bodyText→emailBody, sender→from); added create-if-not-exists logic |
| `src/agents/email_classifier_agent.py` | Two-phase classification with `step` parameter |
| `utils/check_test_docs.py` | New: query/delete test documents |
| `utils/cleanup_pe_events.py` | New: clear PE events container |
| `utils/cleanup_orphans.py` | New: remove docs without status field |
| `.gitignore` | New: Python/VS Code patterns |

## Commands Run

```powershell
# Test flow
python test_flow.py --send          # Send test email to queue
python test_flow.py --process       # Process one email

# Cleanup utilities
python utils/check_test_docs.py --delete
python utils/cleanup_pe_events.py
python utils/cleanup_orphans.py

# Git init
git init
```

## Known Issues / Next Steps

1. **Dashboard verification** - Confirm emails display correctly with new field names
2. **Error handling** - Add retry logic for transient Cosmos failures
3. **Metrics** - Add Application Insights tracking for processing times
4. **Attachments** - Document Intelligence integration not fully tested

## Acceptance Criteria

- [x] Single document per email (no duplicates across partitions)
- [x] Status transitions: received → classified/needs_review/discarded
- [x] PE events deduplicate by hash key
- [x] Emails route to correct queue based on 65% threshold
- [x] Document contains all required fields (from, subject, emailBody, status)
- [ ] Dashboard shows all processed emails with correct status
- [ ] End-to-end test with real O365 email
