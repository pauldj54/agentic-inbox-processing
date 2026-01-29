"""Diagnostic script to check system status."""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent.parent / '.env01'
load_dotenv(env_path)

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient
from azure.cosmos import CosmosClient

ns = os.environ['SERVICEBUS_NAMESPACE']
credential = DefaultAzureCredential()

print('='*60)
print('SERVICE BUS QUEUE STATUS')
print('='*60)

sb_client = ServiceBusClient(
    fully_qualified_namespace=f'{ns}.servicebus.windows.net',
    credential=credential
)

queues = ['email-intake', 'discarded', 'human-review', 'archival-pending']
with sb_client:
    for q in queues:
        try:
            receiver = sb_client.get_queue_receiver(queue_name=q)
            with receiver:
                msgs = receiver.peek_messages(max_message_count=10)
                print(f"{q}: {len(msgs)} message(s)")
                for i, msg in enumerate(msgs[:3]):
                    try:
                        body = json.loads(str(msg))
                        subject = body.get('subject', 'N/A')[:40]
                        sender = body.get('sender', body.get('from', 'N/A'))[:30]
                        print(f"  [{i+1}] From: {sender} | Subject: {subject}")
                    except:
                        print(f"  [{i+1}] Raw: {str(msg)[:80]}...")
        except Exception as e:
            print(f"{q}: Error - {e}")

print()
print('='*60)
print('COSMOS DB PARTITION KEY')
print('='*60)

cosmos_client = CosmosClient(os.environ['COSMOS_ENDPOINT'], credential=credential)
db = cosmos_client.get_database_client('email-processing')
container = db.get_container_client('emails')

props = container.read()
pk_paths = props.get('partitionKey', {}).get('paths', [])
print(f"Partition key: {pk_paths}")

print()
print('='*60)
print('COSMOS DB EMAIL STATUS')
print('='*60)

query = 'SELECT * FROM c ORDER BY c._ts DESC'
emails = list(container.query_items(query=query, enable_cross_partition_query=True))

print(f"Total emails in Cosmos: {len(emails)}")
for e in emails[:10]:
    eid = str(e.get('id', 'N/A'))[:40]
    status = str(e.get('status', 'MISSING'))
    subject = str(e.get('subject', 'N/A'))[:40]
    sender = str(e.get('from', e.get('sender', 'N/A')))[:25]
    received = str(e.get('receivedAt', 'N/A'))[:20]
    print(f"  id: {eid}...")
    print(f"    Status: {status:12} | From: {sender} | Subject: {subject}")
