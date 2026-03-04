# Quickstart: Pipeline Configuration

**Feature**: 002-pipeline-config  
**Branch**: `002-pipeline-config`

## Prerequisites

- Python 3.12+
- Azure CLI (`az login` completed)
- Access to Azure Cosmos DB, Service Bus, Blob Storage, Document Intelligence
- `.env` file with existing required variables (see `run_agent.py`)

## New Environment Variables

Add these to your `.env` file:

```bash
# Pipeline mode: "full" (default) or "triage-only"
PIPELINE_MODE=triage-only

# Queue name for triage-complete messages (default: triage-complete)
TRIAGE_COMPLETE_QUEUE=triage-complete

# Optional: external Service Bus namespace for IDP integration
# If omitted, uses the primary SERVICEBUS_NAMESPACE
# TRIAGE_COMPLETE_SB_NAMESPACE=my-idp-namespace.servicebus.windows.net
```

## Setup

```bash
# 1. Checkout the feature branch
git checkout 002-pipeline-config

# 2. Activate virtual environment
.venv\Scripts\Activate.ps1    # Windows
# source .venv/bin/activate   # Linux/macOS

# 3. Install dependencies (no new deps for this feature)
pip install -r requirements.txt

# 4. Add new env vars to .env
echo "PIPELINE_MODE=triage-only" >> .env
echo "TRIAGE_COMPLETE_QUEUE=triage-complete" >> .env
```

## Running

### Full Pipeline Mode (default, existing behavior)

```bash
# Either omit PIPELINE_MODE or set to "full"
PIPELINE_MODE=full python -m src.agents.run_agent
```

All emails go through: triage → pre-processing → classification → dedup → storage → routing.

### Triage-Only Mode

```bash
PIPELINE_MODE=triage-only python -m src.agents.run_agent
```

Emails go through: triage → pre-processing → route to triage-complete queue.  
Steps 3–5 (classification, dedup, content storage, final routing) are skipped.

## Running Tests

```bash
# Run all tests
pytest

# Run only pipeline config tests
pytest tests/unit/test_pipeline_config.py -v

# Run with coverage
pytest tests/unit/test_pipeline_config.py --cov=src.agents -v
```

## Manual Test Scenarios

### Scenario 1: Triage-Only Mode Stops After Step 2

1. Set `PIPELINE_MODE=triage-only` in `.env`
2. Place a test email in the `email-intake` queue
3. Run the agent: `python -m src.agents.run_agent`
4. Verify:
   - Cosmos DB email document has `pipelineMode: "triage-only"` and `stepsExecuted` without `"classification"`
   - A message appears in the `triage-complete` queue (or configured queue name)
   - No classification or routing to `archival-pending` / `human-review` occurred

### Scenario 2: Full Mode Unchanged

1. Set `PIPELINE_MODE=full` in `.env` (or remove the variable)
2. Place a test email in the `email-intake` queue
3. Run the agent: `python -m src.agents.run_agent`
4. Verify:
   - Cosmos DB email document has `pipelineMode: "full"` and full `stepsExecuted` list
   - Email is routed as before (archival-pending, human-review, or discarded)
   - No messages in `triage-complete` queue

### Scenario 3: External Service Bus Namespace

1. Set `TRIAGE_COMPLETE_SB_NAMESPACE=<external-ns>.servicebus.windows.net`
2. Ensure the managed identity has `Azure Service Bus Data Sender` on the external namespace
3. Run in triage-only mode
4. Verify the message arrives in the external namespace's queue

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ValueError: Invalid PIPELINE_MODE` | Typo in env var | Set to `"full"` or `"triage-only"` exactly |
| `ServiceBusConnectionError` on triage queue send | External namespace unreachable or no RBAC | Check `TRIAGE_COMPLETE_SB_NAMESPACE` value; grant `Azure Service Bus Data Sender` role |
| Emails still classified in triage-only mode | `PIPELINE_MODE` not read | Restart agent after changing `.env`; check `load_environment()` logs |
| Dashboard doesn't show mode badge | Template not updated | Verify `pipeline_mode` in template context; clear browser cache |
| Missing `pipelineMode` on old emails | Expected | Pre-existing docs don't have the field; treated as `"full"` by default |

## Dashboard

The dashboard at `http://localhost:8000/` displays:
- A badge showing the current pipeline mode ("Full Pipeline" or "Triage Only")
- For triage-only emails: classification column shows "Skipped (triage-only)"
