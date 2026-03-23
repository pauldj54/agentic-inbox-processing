# Triage Consumer Client

A Python client that continuously monitors the `triage-complete` queue for new documents and processes them by calling an external API.

## Features

- ✅ Continuous message consumption from Azure Service Bus queue
- ✅ Formatted console output with document details
- ✅ Automatic API call for each document
- ✅ Message acknowledgment after processing
- ✅ Support for both email and SFTP intake sources
- ✅ Automatic fund name extraction for project naming
- ✅ Language detection (English/French)

## Setup

### 1. Install Dependencies

The required dependencies should already be in your `requirements.txt`:
```bash
pip install -r requirements.txt
```

Key packages used:
- `azure-servicebus`
- `azure-identity`
- `python-dotenv`
- `requests`

### 2. Configure Environment Variables

Add these variables to your `.env01` file:

```bash
# Required - already configured
SERVICEBUS_NAMESPACE=sb-docproc-dev-izr2ch55woa3c
TRIAGE_COMPLETE_QUEUE=triage-complete
STORAGE_ACCOUNT_URL=https://stdocprocdevizr2ch55.blob.core.windows.net

# Optional - for external Service Bus namespace
# TRIAGE_COMPLETE_SB_NAMESPACE=sb-external-namespace

# API Configuration - ADD THESE
API_ENDPOINT=https://your-api-endpoint.com/api/process
DATA_MODEL_NAME=Capital Call Statements
DEFAULT_PROJECT_NAME=Agentic Inbox Processing
DEFAULT_ANALYSIS_NAME=Auto-triage Document Processing
DEFAULT_LANGUAGE=en
```

### 3. Configure Azure Permissions

Your Azure identity needs the **Azure Service Bus Data Receiver** role on the triage-complete queue:

```bash
# Get your user principal
$userPrincipal = az ad signed-in-user show --query "id" -o tsv

# Assign role
az role assignment create \
    --role "Azure Service Bus Data Receiver" \
    --assignee $userPrincipal \
    --scope "/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.ServiceBus/namespaces/<namespace>/queues/triage-complete"
```

## Usage

### Running the Consumer

Start the continuous consumer:

```bash
# Activate virtual environment
.venv\Scripts\Activate.ps1  # Windows
source .venv/bin/activate    # Linux/Mac

# Run consumer
python src/triage_consumer.py
```

The consumer will:
1. Connect to the triage-complete queue
2. Wait for new messages
3. Display document details in the console
4. Call your API for each document
5. Acknowledge messages after processing
6. Continue running until you press Ctrl+C

### Testing with Sample Messages

Send a test message to the queue:

```bash
python utils/send_test_triage_message.py
```

This will prompt you to choose:
1. Email with attachments (default)
2. SFTP file upload

The consumer should immediately pick up and process the test message.

## Message Format

The consumer expects triage-complete messages in this format:

```json
{
    "emailId": "AAMkADAzODkwYzEwL...",
    "intakeSource": "email",
    "attachmentPaths": [
        {
            "name": "document.pdf",
            "local_link": "https://storage.blob.core.windows.net/attachments/document.pdf",
            "size": 245760
        }
    ],
    "subject": "Capital Call - Fund II",
    "body": "Email body text...",
    "from_name": "John Doe",
    "from_address": "john.doe@example.com",
    "receivedAt": "2024-06-01T10:30:00Z",
    "processedAt": "2024-06-01T11:00:00Z",
    "hasAttachments": true,
    "attachmentsCount": 1,
    "relevance": {
        "isRelevant": true,
        "confidence": 0.92,
        "initialCategory": "Capital Call",
        "reasoning": "..."
    },
    "pipelineMode": "triage-only",
    "status": "triaged",
    "routing": {
        "sourceQueue": "intake",
        "targetQueue": "triage-complete",
        "routedAt": "2024-06-01T11:00:00Z"
    }
}
```

### SFTP Messages

SFTP messages include additional fields:

```json
{
    "emailId": "sftp-test-20241215-142200",
    "intakeSource": "sftp",
    "originalFilename": "PE_Investment_Report.pdf",
    "fileType": "pdf",
    "blobPath": "sftp-uploads/2024/12/PE_Investment_Report.pdf",
    ...
}
```

## API Request Format

The consumer transforms queue messages into API requests:

```json
{
    "documents": [
        {
            "sas_url": "https://storage.blob.core.windows.net/attachments/document.pdf",
            "document_name": "document.pdf"
        }
    ],
    "project_name": "Extracted Fund Name or Default",
    "analysis_name": "Auto-triage Document Processing",
    "analysis_description": "Auto-processing from email intake - Capital Call - Fund II",
    "data_model_name": "Capital Call Statements",
    "classifier_name": null,
    "language": "en",
    "created_by": "triage_consumer",
    "auto_extract": true,
    "_metadata": {
        "email_id": "AAMkADAzODkwYzEwL...",
        "intake_source": "email",
        "processed_at": "2024-06-01T11:00:00Z"
    }
}
```

## Console Output

When a message is received, the consumer displays:

```
================================================================================
📄 NEW DOCUMENT RECEIVED
================================================================================

📧 Document ID: AAMkADAzODkwYzEwL...
📥 Intake Source: email
📌 Subject: Capital Call - Fund II - Closing #4
👤 From: Adélaïde Riviere
✉️  Email: adelaide.riviere@example.com
🕐 Received: 2024-06-01T10:30:00Z
⚙️  Processed: 2024-06-01T11:00:00Z

📎 Attachments: 2
   1. Capital_Call_Statement.pdf
      🔗 Link: https://storage.blob.core.windows.net/attachments/document.pdf
      💾 Size: 240.00 KB
   2. Supporting_Documents.pdf
      🔗 Link: https://storage.blob.core.windows.net/attachments/document2.pdf
      💾 Size: 500.00 KB

✅ Relevance Score: 92.00%
🏷️  Category: Capital Call
💭 Reasoning: Email contains capital call statement with clear financial...

⚙️  Pipeline Mode: triage-only
📊 Status: triaged
🔀 Routing: intake → triage-complete
================================================================================
```

## Error Handling

- **API Failures**: Messages are still acknowledged even if the API call fails (logged as warning)
- **Parse Errors**: Invalid JSON messages are logged and acknowledged
- **Connection Errors**: Consumer retries automatically on connection issues
- **Keyboard Interrupt**: Clean shutdown on Ctrl+C

## Customization

### Adjust Polling Behavior

Modify the `max_wait_time` parameter in `run_consumer_loop()`:

```python
receiver = servicebus_client.get_queue_receiver(
    queue_name=TRIAGE_QUEUE,
    max_wait_time=30  # Wait up to 30 seconds
)
```

### Process Multiple Messages

Change `max_message_count` to process in batches:

```python
received_msgs = receiver.receive_messages(
    max_message_count=10,  # Process up to 10 messages at once
    max_wait_time=30
)
```

### Custom API Integration

Modify the `build_api_request()` and `call_api()` functions to match your API requirements.

## Troubleshooting

### "No module named azure.servicebus"
```bash
pip install azure-servicebus azure-identity
```

### "SERVICEBUS_NAMESPACE must be set"
Ensure `.env01` exists and contains the required environment variables.

### "Permission denied" on queue
Verify you have the "Azure Service Bus Data Receiver" role assigned.

### No messages received
- Check the queue has messages: `python src/peek_queue.py`
- Verify `TRIAGE_COMPLETE_QUEUE` matches your queue name
- Ensure messages are being sent to the correct namespace

## Related Files

- `src/triage_consumer.py` - Main consumer client
- `utils/send_test_triage_message.py` - Test message sender
- `src/peek_queue.py` - Queue inspection tool
- `src/agents/tools/queue_tools.py` - Queue management tools
