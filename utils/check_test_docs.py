#!/usr/bin/env python3
"""Quick test to verify document handling."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env01'))

from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient

COSMOS_ENDPOINT = "https://cosmos-docproc-dev-izr2ch55woa3c.documents.azure.com:443/"
COSMOS_DATABASE = "email-processing"

def main():
    cred = DefaultAzureCredential()
    client = CosmosClient(COSMOS_ENDPOINT, credential=cred)
    db = client.get_database_client(COSMOS_DATABASE)
    container = db.get_container_client("emails")
    
    # Query for test document
    test_id = "test-capital-call-007"
    docs = list(container.query_items(
        f"SELECT * FROM c WHERE c.id = '{test_id}'",
        enable_cross_partition_query=True
    ))
    
    print(f"Found {len(docs)} documents with id={test_id}")
    for i, doc in enumerate(docs):
        print(f"\nDocument {i+1}:")
        print(f"  status: {doc.get('status')}")
        print(f"  queue: {doc.get('queue')}")
        print(f"  from: {doc.get('from')}")
        print(f"  classification: {doc.get('classification', {}).get('category')}")
    
    # Delete all instances
    if len(docs) > 0 and input("\nDelete all? (y/n): ").lower() == 'y':
        for doc in docs:
            status = doc.get('status')
            if status:
                try:
                    container.delete_item(item=doc['id'], partition_key=status)
                    print(f"Deleted document with status={status}")
                except Exception as e:
                    print(f"Failed to delete: {e}")
        
        # Verify
        remaining = list(container.query_items(
            f"SELECT c.id FROM c WHERE c.id = '{test_id}'",
            enable_cross_partition_query=True
        ))
        print(f"\nRemaining: {len(remaining)} documents")

if __name__ == "__main__":
    main()
