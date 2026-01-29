#!/usr/bin/env python3
"""Clean up orphan documents with null partition key."""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env01')

from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient

credential = DefaultAzureCredential()
cosmos = CosmosClient(os.environ['COSMOS_ENDPOINT'], credential=credential)
db = cosmos.get_database_client('email-processing')
container = db.get_container_client('emails')

# Get all documents
docs = list(container.query_items(
    'SELECT * FROM c',
    enable_cross_partition_query=True
))

print(f"Found {len(docs)} documents")
print()

orphans = []
valid = []

for doc in docs:
    doc_id = doc.get('id', 'unknown')
    status = doc.get('status')
    
    if status is None:
        orphans.append(doc)
        print(f"ORPHAN: {doc_id[:50]}...")
    else:
        valid.append(doc)
        print(f"VALID:  {doc_id[:50]}... (status={status})")

print()
print(f"Valid: {len(valid)}, Orphans: {len(orphans)}")
print()

if orphans:
    print("Deleting orphan documents...")
    for doc in orphans:
        doc_id = doc.get('id')
        # For documents with null/undefined partition key, try with None
        try:
            # Use json.loads(json.dumps(None)) trick for null value
            container.delete_item(item=doc, partition_key=json.loads('null'))
            print(f"  Deleted: {doc_id[:50]}...")
        except Exception as e:
            print(f"  Failed: {doc_id[:50]}... - {str(e)[:100]}")
