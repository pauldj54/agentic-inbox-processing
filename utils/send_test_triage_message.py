"""
Test Message Sender for Triage Queue

Sends a sample triage-complete message to test the consumer.
"""

import os
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage

# Load environment variables
env_path = Path(__file__).parent.parent / ".env01"
load_dotenv(env_path)

# Configuration
SERVICEBUS_NAMESPACE = os.environ.get("SERVICEBUS_NAMESPACE")
TRIAGE_QUEUE = os.environ.get("TRIAGE_COMPLETE_QUEUE", "triage-complete")
TRIAGE_SB_NAMESPACE = os.environ.get("TRIAGE_COMPLETE_SB_NAMESPACE")

namespace = TRIAGE_SB_NAMESPACE or SERVICEBUS_NAMESPACE
if not namespace:
    raise ValueError("SERVICEBUS_NAMESPACE must be set")

FULLY_QUALIFIED_NAMESPACE = f"{namespace}.servicebus.windows.net"


def create_sample_message():
    """Create a sample triage-complete message."""
    return {
        "emailId": "test-" + datetime.utcnow().strftime("%Y%m%d-%H%M%S"),
        "intakeSource": "email",
        "attachmentPaths": [
            {
                "name": "Capital_Call_Statement.pdf",
                "local_link": "https://stdocprocdevizr2ch55.blob.core.windows.net/attachments/test-123/Capital_Call_Statement.pdf",
                "size": 245760
            },
            {
                "name": "Supporting_Documents.pdf",
                "local_link": "https://stdocprocdevizr2ch55.blob.core.windows.net/attachments/test-123/Supporting_Documents.pdf",
                "size": 512000
            }
        ],
        "subject": "Opale Capital Strategies Fonds II - Appel de fonds - Closing #4",
        "body": "Bonjour,\n\nVeuillez trouver ci-joint l'appel de fonds pour le closing #4 du fonds Opale Capital Strategies Fonds II.\n\nCordialement,\nAdélaïde Riviere",
        "from": "adelaide.riviere@example.com",
        "receivedAt": "2024-06-01T10:30:00Z",
        "processedAt": datetime.utcnow().isoformat() + "Z",
        "hasAttachments": True,
        "attachmentsCount": 2,
        "relevance": {
            "isRelevant": True,
            "confidence": 0.92,
            "initialCategory": "Capital Call",
            "reasoning": "Email contains capital call statement with clear financial documentation"
        },
        "pipelineMode": "triage-only",
        "status": "triaged",
        "routing": {
            "sourceQueue": "intake",
            "targetQueue": "triage-complete",
            "routedAt": datetime.utcnow().isoformat() + "Z"
        }
    }


def create_sftp_sample_message():
    """Create a sample SFTP file triage message."""
    return {
        "emailId": "sftp-test-" + datetime.utcnow().strftime("%Y%m%d-%H%M%S"),
        "intakeSource": "sftp",
        "originalFilename": "PE_Investment_Report_Q4_2024.pdf",
        "fileType": "pdf",
        "blobPath": "sftp-uploads/2024/12/PE_Investment_Report_Q4_2024.pdf",
        "attachmentPaths": [
            {
                "name": "PE_Investment_Report_Q4_2024.pdf",
                "local_link": "https://stdocprocdevizr2ch55.blob.core.windows.net/attachments/sftp-uploads/2024/12/PE_Investment_Report_Q4_2024.pdf",
                "size": 1048576
            }
        ],
        "receivedAt": "2024-12-15T14:22:00Z",
        "processedAt": datetime.utcnow().isoformat() + "Z",
        "hasAttachments": True,
        "attachmentsCount": 1,
        "relevance": {
            "isRelevant": True,
            "confidence": 0.88,
            "initialCategory": "Investment Report",
            "reasoning": "SFTP file appears to be a quarterly investment report"
        },
        "pipelineMode": "triage-only",
        "status": "triaged",
        "routing": {
            "sourceQueue": "intake",
            "targetQueue": "triage-complete",
            "routedAt": datetime.utcnow().isoformat() + "Z"
        }
    }


def send_message(message_data: dict):
    """Send message to triage-complete queue."""
    
    print(f"📤 Sending test message to queue: {TRIAGE_QUEUE}")
    print(f"📡 Namespace: {namespace}")
    print(f"\n📦 Message payload:")
    print(json.dumps(message_data, indent=2))
    
    credential = DefaultAzureCredential()
    servicebus_client = ServiceBusClient(
        fully_qualified_namespace=FULLY_QUALIFIED_NAMESPACE,
        credential=credential
    )
    
    try:
        with servicebus_client:
            sender = servicebus_client.get_queue_sender(queue_name=TRIAGE_QUEUE)
            with sender:
                message = ServiceBusMessage(
                    body=json.dumps(message_data, default=str),
                    content_type="application/json"
                )
                sender.send_messages(message)
                print(f"\n✅ Message sent successfully!")
                print(f"📬 Message ID: {message_data.get('emailId')}")
                
    except Exception as e:
        print(f"\n❌ Failed to send message: {e}")
        raise


if __name__ == "__main__":
    import sys
    
    print("="*80)
    print("📨 TRIAGE QUEUE TEST MESSAGE SENDER")
    print("="*80)
    print("\nSelect message type:")
    print("1. Email with attachments (default)")
    print("2. SFTP file")
    print()
    
    choice = input("Enter choice (1 or 2): ").strip() or "1"
    
    if choice == "2":
        message = create_sftp_sample_message()
    else:
        message = create_sample_message()
    
    print()
    send_message(message)
    print("\n✅ Done! Your consumer should pick up this message.\n")
