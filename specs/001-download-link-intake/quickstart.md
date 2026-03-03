# Quickstart: Download-Link Intake

**Feature**: 001-download-link-intake  
**Date**: 2026-02-26

## Prerequisites

1. **Python 3.12+** with virtual environment activated
2. **Azure CLI** logged in (`az login`) — needed for `DefaultAzureCredential`
3. **RBAC**: Your Azure identity must have `Storage Blob Data Contributor` on storage account `stdocprocdevizr2ch55`
4. **Environment variables** in `.env01`:
   ```env
   COSMOS_ENDPOINT=https://cosmos-docproc-dev-....documents.azure.com:443/
   SERVICEBUS_NAMESPACE=sb-docproc-dev-...
   STORAGE_ACCOUNT_URL=https://stdocprocdevizr2ch55.blob.core.windows.net
   ```

## Install New Dependency

```bash
pip install azure-storage-blob>=12.19.0
```

Or update from requirements.txt:
```bash
pip install -r requirements.txt
```

## Test the Feature

### 1. Unit Tests — Link Detection

Run the link detection tests to verify URL extraction and filtering:

```bash
pytest tests/unit/test_link_download_tool.py -v
```

Expected: Tests pass for document-extension URLs, non-document URLs are skipped, HTML href extraction works.

### 2. Manual Test — End-to-End

**Step 1**: Send a test email to the monitored inbox with:
- No traditional attachments
- Body containing a public download link, e.g.: `https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf`

**Step 2**: Wait for the Logic App to trigger and send to Service Bus.

**Step 3**: Run the agent manually:
```bash
python -m src.agents.run_agent
```

**Step 4**: Verify results:
- **Blob Storage**: Check `/attachments/{emailId}/dummy.pdf` exists in the `attachments` container
- **Cosmos DB**: Check the email document in `emails` container — `attachmentPaths` should contain `{"path": "{emailId}/dummy.pdf", "source": "link"}`
- **Dashboard**: Run `uvicorn src.webapp.main:app --reload` and check the email entry shows a link-sourced attachment indicator

### 3. Manual Test — Failure Handling

**Step 1**: Send a test email with a broken link:
- Body containing: `https://httpstat.us/404/download.pdf`

**Step 2**: Run the agent and verify:
- Email is still ingested and classified normally
- `downloadFailures` array in Cosmos DB contains the failure record
- Logs show the failure with URL, HTTP status, and email ID

### 4. Dashboard Verification

1. Start the dashboard: `uvicorn src.webapp.main:app --reload`
2. Navigate to `http://localhost:8000`
3. Find an email with a link-sourced attachment
4. Verify the attachment column shows a visual indicator distinguishing link-sourced from traditional attachments

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ResourceNotFoundError` on blob upload | Missing `attachments` container | Container should already exist (created by Logic App) |
| `AuthenticationError` on blob upload | Missing RBAC role | Assign `Storage Blob Data Contributor` to your identity |
| Download succeeds but blob is empty | Content-Length was 0 or redirect was not followed | Check `aiohttp` redirect settings; verify URL is direct download |
| No URLs detected in email body | HTML body not parsed, or URL doesn't match extension filter | Check logs for `urls_detected` count; verify URL has document extension |
