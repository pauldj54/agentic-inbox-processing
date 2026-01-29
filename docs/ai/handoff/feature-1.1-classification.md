# Feature 1.1: Email Classification Pipeline - Two-Phase Classification

## Goal & Current Status

Built an email classification pipeline that receives emails via Logic App → Service Bus → AI Agent → Cosmos DB, with PE event deduplication.

**Status: UPDATED** - Implemented clear two-phase classification with binary PE relevance check and 10 PE event types.

## Classification Logic (Updated)

### Phase 1: PE Relevance Check (Binary Decision)
- **Input**: Email metadata + attachment names (attachment names have HIGHEST weight)
- **Output**: `is_relevant` = true/false
- **If NOT PE-relevant**: Route to `discarded` queue immediately (end processing)
- **If PE-relevant**: Proceed to Phase 2

### Phase 2: PE Event Type Classification
- **Input**: Email + attachment content (extracted via Document Intelligence)
- **Output**: One of 10 PE event types + confidence score
- **Routing**:
  - Confidence ≥ 65% → `archival-pending` queue
  - Confidence < 65% → `human-review` queue

### Valid PE Event Types (10 categories)
1. Capital Call (Drawdown Notice)
2. Distribution Notice
3. Capital Account Statement (NAV Statement)
4. Quarterly Report
5. Annual Financial Statement
6. Tax Statement (K-1, Tax Voucher)
7. Legal Notice (Amendment, Consent Request)
8. Subscription Agreement (Initial Investment)
9. Extension Notice (Fund Term Extension)
10. Dissolution Notice (Final Fund Closure)

## Decisions & Constraints

- **Partition key is `/status`** - changing status requires delete-then-upsert (Cosmos limitation)
- **Confidence threshold: 65%** - ≥65% → `archival-pending`, <65% → `human-review`
- **4 queues**: `email-intake`, `discarded`, `human-review`, `archival-pending`
- **Field mapping**: Logic App sends `bodyText`/`sender`, agent expects `emailBody`/`from` - code handles both
- **Two-phase classification**: relevance check (binary) → PE event classification (with confidence)
- **PE deduplication**: Hash of `pe_company|fund_name|category|amount|due_date` stored in `pe-events` container
- **Attachment names have HIGHEST WEIGHT** in relevance check (50% weight)
- **Document Intelligence output minimized** - only tables (first 5, 15 rows each) and text (first 3000 chars)

## Architecture Snapshot

```
Logic App (O365 trigger)
    ↓ JSON message
Service Bus (email-intake queue)
    ↓ receive + complete
EmailClassificationAgent
    │
    ├── Phase 1: PE Relevance Check (binary)
    │   ├── is_relevant=false → discarded queue (END)
    │   └── is_relevant=true → continue
    │
    ├── Phase 2: PE Event Classification
    │   ├── Process attachments (Document Intelligence)
    │   ├── Classify into 1 of 10 PE event types
    │   └── confidence >= 65%? → archival-pending
    │       confidence <  65%? → human-review
    │
    └── PE event dedup check → pe-events container
    ↓
Cosmos DB
    ├── emails (partition: /status)
    ├── pe-events (partition: /eventType)
    ├── classifications
    └── audit-logs
```

## Files Touched (This Update)

| Path | Change |
|------|--------|
| `src/agents/classification_prompts.py` | Updated prompts: binary relevance check with attachment name weighting (50%), removed "Unknown" category, added 10 PE event types |
| `src/agents/email_classifier_agent.py` | Updated two-phase logic with clear routing, enhanced attachment name handling in relevance check, added `parse_bool()` helper for string booleans, added HTML-to-text extraction, added override logic for PE subjects |
| `src/agents/tools/document_intelligence_tool.py` | Minimized output: tables (first 5, 15 rows), text (3000 chars), removed verbose paragraphs/key-value pairs |
| `docs/ai/handoff/feature-1.1-classification.md` | Updated handoff documentation |

## Previously Touched Files

| Path | Change |
|------|--------|
| `src/agents/tools/cosmos_tools.py` | Fixed duplicate doc bug: capture `old_status` BEFORE modifying doc; added field name mapping (bodyText→emailBody, sender→from); added create-if-not-exists logic |
| `utils/check_test_docs.py` | New: query/delete test documents |
| `utils/cleanup_pe_events.py` | New: clear PE events container |
| `utils/cleanup_orphans.py` | New: remove docs without status field |
| `.gitignore` | New: Python/VS Code patterns |

## Known Issues / Next Steps

1. **Logic App needs to send attachment names** - Currently sends `hasAttachments: "True"` (string!) and `attachmentCount: "2"` but NOT the attachment filenames. Add `attachmentPaths` array with filenames.
2. **Error handling** - Add retry logic for transient Cosmos failures
3. **Metrics** - Add Application Insights tracking for processing times

## Logic App Data Format Issues (CRITICAL)

The Logic App is sending data with these issues that the agent now handles:

| Field | Expected | Actual (from Logic App) | Fix Applied |
|-------|----------|------------------------|-------------|
| `hasAttachments` | `true` (boolean) | `"True"` (string) | Added `parse_bool()` helper |
| `attachmentCount` | `2` (number) | `"2"` (string) | Added string-to-int parsing |
| `attachmentPaths` | `["file1.pdf", "file2.pdf"]` | Missing! | Override logic if subject has "PE" |
| `emailBody` | Plain text | HTML with tags | Added `extract_plain_text_from_html()` |

### Recommended Logic App Fix

Update the Logic App to send proper JSON:
```json
{
  "hasAttachments": true,           // boolean, not string
  "attachmentCount": 2,             // number, not string  
  "attachmentPaths": [              // ADD THIS FIELD
    "Opale Capital Strategies Fonds II - Appel de fonds.pdf"
  ]
}
```

## Acceptance Criteria

- [x] Single document per email (no duplicates across partitions)
- [x] Status transitions: received → classified/needs_review/discarded
- [x] PE events deduplicate by hash key
- [x] Emails route to correct queue based on 65% threshold
- [x] Document contains all required fields (from, subject, emailBody, status)
- [x] Dashboard shows all processed emails with correct status
- [x] End-to-end test with real O365 email
- [x] Handle string booleans from Logic App ("True"/"False")
- [x] Extract plain text from HTML email bodies
- [x] Override relevance check if subject contains "PE" and has attachments