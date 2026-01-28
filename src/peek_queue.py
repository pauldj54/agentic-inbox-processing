"""
Peek messages from Azure Service Bus queue using DefaultAzureCredential.
Uses the signed-in user from VS Code or Azure CLI for authentication.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient

# Load environment variables from .env01 in the project root
env_path = Path(__file__).parent.parent / ".env01"
load_dotenv(env_path)

# Configuration from environment
SERVICEBUS_NAMESPACE = os.environ["SERVICEBUS_NAMESPACE"]
QUEUE_NAME = os.environ["SERVICEBUS_QUEUE_NAME"]
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "10"))

# Build the fully qualified namespace
FULLY_QUALIFIED_NAMESPACE = f"{SERVICEBUS_NAMESPACE}.servicebus.windows.net"


def peek_queue_messages():
    """Peek messages from the queue without consuming them."""
    
    # Use DefaultAzureCredential (picks up VS Code / Azure CLI credentials)
    credential = DefaultAzureCredential()
    
    servicebus_client = ServiceBusClient(
        fully_qualified_namespace=FULLY_QUALIFIED_NAMESPACE,
        credential=credential
    )

    total_peeked = 0
    
    with servicebus_client:
        receiver = servicebus_client.get_queue_receiver(queue_name=QUEUE_NAME)
        
        with receiver:
            # Peek first batch
            peeked_msgs = receiver.peek_messages(max_message_count=min(MAX_MESSAGES, 250))
            
            while peeked_msgs and total_peeked < MAX_MESSAGES:
                for msg in peeked_msgs:
                    total_peeked += 1
                    print(f"\n{'='*60}")
                    print(f"Message #{total_peeked}")
                    print(f"Sequence Number: {msg.sequence_number}")
                    print(f"Enqueued Time: {msg.enqueued_time_utc}")
                    print(f"Content Type: {msg.content_type}")
                    print(f"Body:\n{str(msg)}")
                    
                    if total_peeked >= MAX_MESSAGES:
                        break
                
                # Check if we need more messages
                if total_peeked >= MAX_MESSAGES:
                    break
                    
                # Peek next batch starting from the next sequence number
                from_seq_num = peeked_msgs[-1].sequence_number + 1
                remaining = MAX_MESSAGES - total_peeked
                peeked_msgs = receiver.peek_messages(
                    max_message_count=min(remaining, 250),
                    sequence_number=from_seq_num
                )

    print(f"\n{'='*60}")
    print(f"Peek complete. Total messages: {total_peeked}")


if __name__ == "__main__":
    peek_queue_messages()
