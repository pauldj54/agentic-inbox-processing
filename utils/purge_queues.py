#!/usr/bin/env python3
"""
Purge All Service Bus Queues
============================
Removes all messages from all queues used in the email processing workflow.

Usage:
    python utils/purge_queues.py [--dry-run]

Options:
    --dry-run    Show what would be deleted without actually deleting
"""

import os
import sys
import argparse
from pathlib import Path

# Load environment from .env01
env_file = Path(__file__).parent.parent / ".env01"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

# Queue names used in the workflow
QUEUE_NAMES = [
    "email-intake",
    os.environ.get("DISCARDED_QUEUE", "discarded"),
    os.environ.get("HUMAN_REVIEW_QUEUE", "human-review"),
    os.environ.get("ARCHIVAL_PENDING_QUEUE", "archival-pending"),
    os.environ.get("TRIAGE_COMPLETE_QUEUE", "triage-complete"),
]


def purge_queue(client, queue_name: str, dry_run: bool = False) -> int:
    """
    Purge all messages from a queue.
    
    Args:
        client: ServiceBusClient instance
        queue_name: Name of the queue to purge
        dry_run: If True, only count messages without deleting
        
    Returns:
        Number of messages deleted/counted
    """
    count = 0
    
    try:
        receiver = client.get_queue_receiver(
            queue_name=queue_name,
            max_wait_time=5
        )
        
        with receiver:
            if dry_run:
                # Just peek and count
                messages = receiver.peek_messages(max_message_count=1000)
                count = len(messages)
                print(f"  📋 {queue_name}: {count} messages would be deleted")
            else:
                # Receive and complete all messages
                while True:
                    messages = receiver.receive_messages(
                        max_message_count=100,
                        max_wait_time=2
                    )
                    if not messages:
                        break
                    
                    for msg in messages:
                        receiver.complete_message(msg)
                        count += 1
                
                print(f"  🗑️  {queue_name}: {count} messages deleted")
                
    except Exception as e:
        print(f"  ❌ {queue_name}: Error - {e}")
        
    return count


def main():
    parser = argparse.ArgumentParser(
        description="Purge all messages from Service Bus queues"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting"
    )
    parser.add_argument(
        "--queue",
        type=str,
        help="Purge only a specific queue (default: all queues)"
    )
    args = parser.parse_args()
    
    namespace = os.environ.get("SERVICEBUS_NAMESPACE")
    if not namespace:
        print("❌ SERVICEBUS_NAMESPACE not configured")
        sys.exit(1)
    
    print("=" * 60)
    if args.dry_run:
        print("Service Bus Queue Purge (DRY RUN)")
    else:
        print("Service Bus Queue Purge")
    print("=" * 60)
    print(f"Namespace: {namespace}")
    print()
    
    # Confirm if not dry run
    if not args.dry_run:
        queues_to_purge = [args.queue] if args.queue else QUEUE_NAMES
        print(f"⚠️  This will DELETE all messages from: {', '.join(queues_to_purge)}")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() != "yes":
            print("Cancelled.")
            sys.exit(0)
        print()
    
    try:
        from azure.identity import DefaultAzureCredential
        from azure.servicebus import ServiceBusClient
        
        credential = DefaultAzureCredential()
        client = ServiceBusClient(
            fully_qualified_namespace=f"{namespace}.servicebus.windows.net",
            credential=credential
        )
        
        total_deleted = 0
        queues_to_process = [args.queue] if args.queue else QUEUE_NAMES
        
        with client:
            for queue_name in queues_to_process:
                count = purge_queue(client, queue_name, args.dry_run)
                total_deleted += count
        
        print()
        print("=" * 60)
        if args.dry_run:
            print(f"Total messages that would be deleted: {total_deleted}")
        else:
            print(f"✅ Total messages deleted: {total_deleted}")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
