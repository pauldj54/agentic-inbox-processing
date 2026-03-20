#!/usr/bin/env python3
"""Quick check of system status."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment
load_dotenv(Path(__file__).parent.parent / ".env01")

from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient
from azure.servicebus import ServiceBusClient

credential = DefaultAzureCredential()

# Check Cosmos DB
print("=" * 60)
print("COSMOS DB - Recent Emails")
print("=" * 60)

cosmos = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=credential)
container = cosmos.get_database_client("email-processing").get_container_client("emails")

query = "SELECT c.id, c.subject, c.status, c.receivedAt, c._ts FROM c ORDER BY c._ts DESC"
emails = list(container.query_items(query, enable_cross_partition_query=True))

print(f"Total emails: {len(emails)}")
print()
for e in emails[:10]:
    ts = e.get("_ts", 0)
    status = e.get("status", "?")
    subj = (e.get("subject") or "")[:40]
    eid = e.get("id", "?")[:40]
    print(f"  [{status:12}] {subj}")

# Check queues
print()
print("=" * 60)
print("SERVICE BUS - Queue Status")
print("=" * 60)

namespace = os.environ["SERVICEBUS_NAMESPACE"]
sb = ServiceBusClient(f"{namespace}.servicebus.windows.net", credential=credential)

queues = [
    os.environ.get("INTAKE_QUEUE", "intake"),
    os.environ.get("DISCARDED_QUEUE", "discarded"),
    os.environ.get("HUMAN_REVIEW_QUEUE", "human-review"),
    os.environ.get("ARCHIVAL_PENDING_QUEUE", "archival-pending"),
]
with sb:
    for q in queues:
        try:
            receiver = sb.get_queue_receiver(q)
            with receiver:
                msgs = receiver.peek_messages(max_message_count=100)
                print(f"  {q}: {len(msgs)} messages")
        except Exception as e:
            print(f"  {q}: ERROR - {e}")

print()
print("=" * 60)
