# Quickstart: Attachment Delivery Tracking Verification

**Feature**: 006-attachment-delivery-tracking  
**Date**: 2026-03-30

## Prerequisites

- Azure subscription with deployed resources (Logic App, Cosmos DB, Blob Storage, App Service)
- Access to the monitored email inbox
- Azure CLI authenticated (`az login`)
- Python virtual environment activated with project dependencies

## Test 1: Email Attachment — New Record with Delivery Tracking

1. Send an email with a PDF attachment to the monitored inbox from a known sender domain
2. Wait ~1 minute for the email Logic App to trigger
3. Query Cosmos DB for the new record:
   ```bash
   python utils/query_cosmos.py --query "SELECT * FROM c WHERE c.subject = '<your subject>' ORDER BY c.receivedAt DESC"
   ```
4. **Verify**: Record has `contentHash` (non-null base64 string), `version: 1`, `deliveryCount: 1`, `deliveryHistory` with one entry (`action: "new"`), and `lastDeliveredAt` populated

## Test 2: Email Attachment — Duplicate Detection

1. Send a second email from the same sender domain with the **identical PDF** attachment
2. Wait ~1 minute for processing
3. Query Cosmos DB again for records from that sender domain:
   ```bash
   python utils/query_cosmos.py --query "SELECT * FROM c WHERE c.contentHash = '<hash from test 1>'"
   ```
4. **Verify**: The original record now has `deliveryCount: 2`, `deliveryHistory` with two entries (second has `action: "duplicate"`), and `lastDeliveredAt` updated. No new duplicate record created.

## Test 3: Email Attachment — Content Update Detection

1. Send a third email from the same sender domain with a PDF that has the **same filename** but **different content**
2. Wait ~1 minute for processing
3. Query the record
4. **Verify**: If filename-based update detection matches, record has `version: 2`, `deliveryCount: 3`, `contentHash` updated to new value, and history entry with `action: "update"`

## Test 4: Dashboard Badge Visibility

1. Open the dashboard: `https://app-docproc-dev-izr2ch55woa3c.azurewebsites.net/`
2. Find the email record from Test 1 in the table
3. **Verify**: Version badge shows "v1" and delivery count shows "2x" (or "3x" after Test 3) — same format as SFTP records
4. **Verify**: Legacy email records (without delivery tracking) still show "—"

## Test 5: Link Download — Delivery Tracking

1. Send an email with a download link to a PDF document
2. Wait for the triage consumer to process the email and download the link
3. Query Cosmos DB for the record
4. **Verify**: Record has `contentHash`, `version: 1`, `deliveryCount: 1` populated after link download processing

## Quick Cosmos DB Verification Commands

```bash
# Check a specific record's delivery tracking fields
python utils/query_cosmos.py --query "SELECT c.id, c.contentHash, c.version, c.deliveryCount, c.lastDeliveredAt, c.intakeSource FROM c WHERE c.intakeSource = 'email' AND c.version != null ORDER BY c.receivedAt DESC OFFSET 0 LIMIT 5"

# Check SFTP records still work (regression)
python utils/query_cosmos.py --query "SELECT c.id, c.version, c.deliveryCount FROM c WHERE c.intakeSource = 'sftp' ORDER BY c.receivedAt DESC OFFSET 0 LIMIT 3"
```
