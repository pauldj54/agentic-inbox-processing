# Quickstart: SFTP File Intake Channel

**Feature**: 003-sftp-intake  
**Date**: 2026-03-09

## Prerequisites

1. **Python 3.12+** with virtual environment activated
2. **Azure CLI** logged in (`az login`) — needed for `DefaultAzureCredential`
3. **RBAC**: Your Azure identity must have:
   - `Storage Blob Data Contributor` on storage account `stdocprocdevizr2ch55`
   - `Cosmos DB Data Contributor` on Cosmos DB account
   - `Azure Service Bus Data Sender` + `Data Receiver` on Service Bus namespace
4. **Infrastructure provisioned**: The following resources must be deployed via Bicep before testing:
   - **Key Vault secrets**: `sftp-private-key` (SSH private key for SFTP) and `sharepoint-client-secret` (Entra ID app client secret)
   - **API Connection resources**: `sftpwithssh` (SSH key from Key Vault) and `sharepointonline` (Entra ID client credentials from Key Vault)
   - See [research.md §2 and §9](research.md) for provisioning details
5. **Environment variables** in `.env`:
   ```env
   COSMOS_ENDPOINT=https://cosmos-docproc-dev-....documents.azure.com:443/
   SERVICEBUS_NAMESPACE=sb-docproc-dev-...
   STORAGE_ACCOUNT_URL=https://stdocprocdevizr2ch55.blob.core.windows.net
   PIPELINE_MODE=full
   ```
6. **Cosmos DB migration completed**: The `intake-records` container must exist (migrated from `emails`).

## No New Dependencies

All required Azure SDKs are already in `requirements.txt`. No new packages needed.

## Test the Feature

### 1. Unit Tests — Container Rename

Verify the container rename doesn't break existing functionality:

```bash
pytest tests/unit/ -v
```

Expected: All existing tests pass with the updated `CONTAINER_INTAKE_RECORDS` constant.

### 2. Integration Tests — SFTP Intake Flow

```bash
pytest tests/integration/test_sftp_intake_flow.py -v
```

Expected: Tests cover CSV/Excel direct archival, PDF classification routing, duplicate detection.

### 3. Manual Test — CSV/Excel SharePoint Upload

**Step 1**: Place a CSV file on the SFTP server with a valid filename convention:
```
/inbox/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv
```

**Step 2**: Wait for the SFTP Logic App to trigger (polling interval: ~1 minute).

**Step 3**: Verify results:
- **Blob Storage**: Check `/attachments/sftp-{fileId}/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv` exists in the `attachments` container
- **Cosmos DB**: Check the `intake-records` container for a record with `intakeSource: "sftp"`, `fileType: "csv"`, `status: "archived"`, parsed metadata (`account: "HorizonCapital"`, `fund: "GrowthFundIII"`, etc.), and `sharepointPath` populated
- **SharePoint**: Verify file uploaded to `{root}/H/HorizonCapital/GrowthFundIII/{filename}` in the document library
- **Service Bus**: No message sent to any queue (CSV/Excel bypass Service Bus)
- **SFTP server**: File moved to `/processed/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv`

### 3.5. Manual Test — Filename Parse Failure

**Step 1**: Place a CSV file with an invalid filename (wrong number of segments):
```
/inbox/bad-filename.csv
```

**Step 2**: Wait for the SFTP Logic App to trigger.

**Step 3**: Verify results:
- **Cosmos DB**: Record created with `status: "error"`, `metadataParseError` populated
- **SharePoint**: No file uploaded
- **SFTP server**: File remains in `/inbox/` (NOT moved to `/processed/`)

### 4. Manual Test — PDF Classification (Full Mode)

**Step 1**: Place a PDF file on the SFTP server with a valid filename convention:
```
/inbox/HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf
```

**Step 2**: Wait for the SFTP Logic App to trigger.

**Step 3**: Run the agent manually:
```bash
python -m src.agents.run_agent
```

**Step 4**: Verify results:
- **Blob Storage**: Check `/attachments/sftp-{fileId}/HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf` exists
- **Cosmos DB**: Check `intake-records` for `intakeSource: "sftp"`, `fileType: "pdf"`, parsed metadata (`account: "HorizonCapital"`, etc.), classification populated
- **Routing**: Based on confidence — `archival-pending` (≥65%), `human-review` (<65%), or `discarded`

### 5. Manual Test — PDF Triage-Only Mode

**Step 1**: Set `PIPELINE_MODE=triage-only` in environment.

**Step 2**: Place a PDF file on the SFTP server with a valid filename:
```
/inbox/HorizonCapital_GrowthFundIII_CapitalCall_Q4Notice_20260309_20260401.pdf
```

**Step 3**: Run the agent:
```bash
python -m src.agents.run_agent
```

**Step 4**: Verify:
- Cosmos DB record has `pipelineMode: "triage-only"`, `stepsExecuted` does not include `"classification"`
- Message routed to the `triage-complete` queue

### 6. Manual Test — Duplicate Detection

**Step 1**: Place the same file on the SFTP server twice (same path, same content).

**Step 2**: Verify only one Cosmos DB record is created. Second detection is logged and skipped. For CSV/Excel, only one SharePoint upload occurs. For PDF, only one Service Bus message is sent.

### 7. Manual Test — Unsupported File Type

**Step 1**: Place a `.docx` file on the SFTP server.

**Step 2**: Verify:
- No Cosmos DB record created
- No Service Bus message sent
- Logic App run shows a warning log entry
- File remains in `/inbox/` (not moved to `/processed/`)

### 8. Dashboard Verification

1. Start the dashboard: `uvicorn src.webapp.main:app --reload`
2. Navigate to `http://localhost:8000`
3. Verify:
   - SFTP-sourced records appear alongside email records
   - "Source" column shows "Email" or "SFTP" badges
   - SFTP records display `originalFilename` instead of `subject`
   - SFTP CSV/Excel records display `sharepointPath`
   - Existing email records still display correctly (backward compatibility)

### 9. Migration Verification

After running the container migration:

```bash
python -m utils.migrate_container
```

Verify:
- All documents from `emails` container exist in `intake-records`
- Each migrated document has `intakeSource: "email"`
- Dashboard loads from `intake-records` container
- Agent processes queue messages using `intake-records` container

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Logic App trigger doesn't fire | SFTP connection failed | Check Key Vault `sftp-private-key` secret, verify SFTP host/port, check Logic App run history |
| File processed twice | Archive move failed on first run | Duplicate detection should catch this. Check `contentHash` dedup query in Logic App. |
| Agent skips SFTP message | `intakeSource` check not detecting `"sftp"` | Verify Logic App sends `intakeSource: "sftp"` in Service Bus message |
| SharePoint upload fails | Auth or folder path issue | Verify Entra ID app registration has `Sites.ReadWrite.All`; check Key Vault `sharepoint-client-secret`; check folder path construction |
| Filename parsing error | Filename doesn't match `{A}_{F}_{D}_{N}_{PD}_{ED}.ext` | Check delimiter config; verify SFTP naming convention. File stays on SFTP with `status: "error"` |
| Dashboard shows empty | Container name mismatch | Ensure `main.py` references `intake-records`, not `emails` |
| `ResourceNotFoundError` on Cosmos query | Container not migrated | Run `python -m utils.migrate_container` first |
| PDF classification fails | Agent can't read blob content | Verify blob path format: `/attachments/sftp-{fileId}/{filename}` |
| Legacy emails missing `intakeSource` | Migration script not run | Run migration or ensure dashboard defaults to `"email"` for records without `intakeSource` |
