# Quickstart: Triage Consumer Client

**Feature**: 005-triage-consumer

## Prerequisites

- Python 3.12+ with virtual environment activated (`.venv`)
- Azure CLI logged in (`az login`) for `DefaultAzureCredential`
- `.env01` file at repo root with `SERVICEBUS_NAMESPACE` set
- Access to the `triage-complete` queue on the Service Bus namespace

## Setup

```bash
# Activate virtual environment
.venv\Scripts\Activate.ps1    # Windows
source .venv/bin/activate      # macOS/Linux

# Install dependencies (if requests not already installed)
pip install requests
```

## Run the Consumer

```bash
python src/triage_consumer.py
```

Expected output:
```
================================================================================
🔃 TRIAGE CONSUMER CLIENT STARTING
================================================================================
📍 Service Bus Namespace: sb-docproc-dev-izr2ch55woa3c
📼 Listening to queue: triage-complete
🔗 API Endpoint: https://your-api-endpoint.com/process
⏳ Waiting for messages... (Press Ctrl+C to stop)
================================================================================

✓ Connected to queue. Waiting for messages...
```

## Send a Test Message

In a separate terminal:

```bash
python utils/send_test_triage_message.py
```

Choose "1" for email or "2" for SFTP, then switch back to the consumer terminal to see the message displayed.

## Configure API Endpoint

Set the `API_ENDPOINT` environment variable in `.env01`:

```ini
API_ENDPOINT=https://your-actual-api.com/process
```

Or export it directly:

```bash
$env:API_ENDPOINT = "https://your-actual-api.com/process"
```

## Stop

Press `Ctrl+C` in the consumer terminal. The consumer will close the Service Bus connection gracefully.
