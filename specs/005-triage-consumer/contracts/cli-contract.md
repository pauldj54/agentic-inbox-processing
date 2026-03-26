# CLI Contract: Triage Consumer Client

**Feature**: 005-triage-consumer
**Date**: 2025-07-17

## Interface Type

Command-line tool — two scripts with no interactive prompts during runtime.

---

## 1. Consumer CLI (`src/triage_consumer.py`)

### Invocation

```bash
python src/triage_consumer.py
```

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICEBUS_NAMESPACE` | Yes* | — | Primary Service Bus namespace (short name, without `.servicebus.windows.net`) |
| `TRIAGE_COMPLETE_SB_NAMESPACE` | No | Falls back to `SERVICEBUS_NAMESPACE` | Override namespace for the triage-complete queue |
| `TRIAGE_COMPLETE_QUEUE` | No | `triage-complete` | Queue name to listen on |
| `API_ENDPOINT` | No | `https://your-api-endpoint.com/process` | Target API endpoint for document forwarding |
| `DATA_MODEL_NAME` | No | `Capital Call Statements` | Data model name in API payload |
| `DEFAULT_PROJECT_NAME` | No | `Agentic Inbox Processing` | Fallback project name when no fund name detected |
| `DEFAULT_ANALYSIS_NAME` | No | `Auto-triage Document Processing` | Analysis name in API payload |
| `DEFAULT_LANGUAGE` | No | `en` | Default language code |
| `STORAGE_ACCOUNT_URL` | No | — | Storage account base URL (used for SAS URL construction) |

*\* Either `SERVICEBUS_NAMESPACE` or `TRIAGE_COMPLETE_SB_NAMESPACE` must be set.*

### Behavior

1. Connects to Azure Service Bus using `DefaultAzureCredential`
2. Opens a receiver on the configured queue
3. Polls for messages with 30-second timeout
4. For each message: parse JSON → display formatted output → build API request → POST to API → complete message
5. Loops continuously until Ctrl+C

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | Clean shutdown via Ctrl+C |
| 1 | Configuration error (missing required env vars) |
| Non-zero | Unhandled fatal error |

### Terminal Output Format

```
================================================================================
📄 NEW DOCUMENT RECEIVED
================================================================================

📌 Document ID: AAMkADI5NmFl...
📑 Intake Source: email
📎 Subject: Capital Call - Fonds Immobilier III
👤 From: investments@example.com
📅 Received: 2025-07-17T10:30:00Z
⚙️ Processed: 2025-07-17T10:30:15Z

📂 Attachments: 2
   1. Capital_Call_Statement.pdf
      🔗 Link: https://stdocprocdev.../Capital_Call_Statement.pdf
      📊 Size: 240.00 KB

✓ Relevance Score: 95.00%
🏷️ Category: Capital Call
💡 Reasoning: Subject contains 'Capital Call' and attachments include...

⚙️ Pipeline Mode: triage-only
📈 Status: triaged
🔃 Routing: email-intake → triage-complete
================================================================================
```

---

## 2. Test Utility CLI (`utils/send_test_triage_message.py`)

### Invocation

```bash
python utils/send_test_triage_message.py
```

### Environment Variables

Same Service Bus variables as the consumer (`SERVICEBUS_NAMESPACE`, `TRIAGE_COMPLETE_SB_NAMESPACE`, `TRIAGE_COMPLETE_QUEUE`).

### Interactive Prompts

```
Choose message type:
1. Email (with attachments)
2. SFTP file
Enter choice (1 or 2):
```

### Behavior

1. Prompts user for message type selection
2. Constructs a realistic sample triage message matching the schema from the email classifier agent
3. Sends the message to the triage-complete queue
4. Displays confirmation with message ID

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | Message sent successfully |
| 1 | Configuration error or send failure |
