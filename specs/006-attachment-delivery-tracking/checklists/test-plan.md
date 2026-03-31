# Test Plan: 006 — Attachment Delivery Tracking

**Feature**: Version and delivery badge tracking for email attachments & download links  
**Date**: 2026-03-30  
**Status**: Ready to execute  

---

## Scope

| Area | What changed | Test type |
|------|-------------|-----------|
| Email Logic App (`workflow.json`) | `Get_attachment_md5` HEAD, `Compute_primary_hash`, delivery tracking fields on Cosmos upsert | E2E |
| `cosmos_tools.py` | `find_by_content_hash()`, `increment_delivery_count()` | Unit |
| `link_download_tool.py` | `content_md5` on `DownloadedFile`, MD5 computation after download | Unit |
| `dashboard.html` | Guard changed from `intakeSource == 'sftp'` to `version is defined` | E2E + Visual |
| SFTP (unchanged) | Regression — badges must still render | E2E |

---

## Unit Tests (automated — `pytest tests/unit/ -v`)

All 9 tests in `tests/unit/test_delivery_tracking.py`:

| # | Test | Pass criteria |
|---|------|---------------|
| U1 | `DownloadedFile.content_md5` defaults to `None` | Field exists, default is None |
| U2 | `DownloadedFile.content_md5` accepts explicit value | Value round-trips |
| U3 | MD5 computation matches `hashlib.md5` | Base64-encoded digest matches |
| U4 | `find_by_content_hash` returns `None` for empty hash | Short-circuit, no Cosmos call |
| U5 | `find_by_content_hash` returns matching record | Query returns record from mock |
| U6 | `find_by_content_hash` returns `None` for no match | Empty result set → None |
| U7 | `increment_delivery_count` — duplicate action | `deliveryCount` +1, version unchanged, history appended |
| U8 | `increment_delivery_count` — update action | `deliveryCount` +1, `version` +1, hash updated |
| U9 | `increment_delivery_count` — history entry structure | Has `deliveredAt`, `contentHash`, `action` fields |

**Run**: `python -m pytest tests/unit/test_delivery_tracking.py -v`

---

## E2E Tests (manual — requires deployed environment)

### T1 — New email creates record with delivery tracking fields

1. Send email with a PDF attachment to the monitored inbox
2. Wait ~1 min for Logic App trigger
3. Query: `python utils/query_cosmos.py --query "SELECT * FROM c WHERE c.subject = '<subject>' ORDER BY c.receivedAt DESC"`
4. **PASS if**: `contentHash` is non-null base64, `version` = 1, `deliveryCount` = 1, `deliveryHistory` has 1 entry with `action: "new"`, `lastDeliveredAt` populated

### T2 — Duplicate email increments delivery count

1. Resend the **exact same PDF** from the same sender domain
2. Wait ~1 min
3. Query by `contentHash` from T1
4. **PASS if**: `deliveryCount` = 2, `deliveryHistory` has 2 entries (second `action: "duplicate"`), `lastDeliveredAt` updated, no duplicate record created

### T3 — Dashboard shows badges for email records

1. Open `https://app-docproc-dev-izr2ch55woa3c.azurewebsites.net/`
2. Locate the email record from T1/T2
3. **PASS if**: Version badge shows **v1**, delivery count shows **2x**
4. **PASS if**: Legacy email records (no tracking fields) show **—**

### T4 — Link download populates tracking fields

1. Send email with a download link to a PDF
2. Wait for triage consumer to process + download
3. Query the record
4. **PASS if**: After processing, `contentHash` populated on the Cosmos record, `content_md5` field set on downloaded file metadata

### T5 — SFTP regression

1. Drop a file via SFTP (or check most recent SFTP record)
2. Check dashboard
3. **PASS if**: SFTP records still show version badge and delivery count — no change in behavior

### T6 — Email with no attachments

1. Send a plain-text email (no attachments)
2. Wait for processing
3. **PASS if**: Record created with `contentHash: null`, `version: 1`, `deliveryCount: 1` — no error from `Compute_primary_hash`

### T7 — Multiple attachments

1. Send email with 2+ PDF attachments
2. **PASS if**: `contentHash` = first attachment's MD5, each `attachmentPaths[]` entry has `contentMd5` field

---

## Regression Suite

Run the full unit test suite to confirm nothing broke:

```
python -m pytest tests/unit/ -v
```

**PASS if**: All 116+ tests pass (including the 9 new delivery tracking tests).

---

## Execution Order

1. Run unit tests (automated) — gate for proceeding
2. Deploy via `.\deploy_updates.ps1`
3. Execute T1 → T2 → T3 (email chain)
4. Execute T4 (link download)
5. Execute T5 (SFTP regression)
6. Execute T6, T7 (edge cases)
