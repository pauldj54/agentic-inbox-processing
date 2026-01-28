#!/usr/bin/env python3
"""
Cleanup script to remove orphan documents from Cosmos DB emails container.
These are documents that were created without a proper 'status' field (partition key).
"""
import os
import sys
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment from .env01
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env01')
load_dotenv(env_path)

from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError

COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT", "https://cosmos-docproc-dev-izr2ch55woa3c.documents.azure.com:443/")
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "email-processing")

def main():
    print("=" * 60)
    print("Cosmos DB Orphan Document Cleanup")
    print("=" * 60)
    
    cred = DefaultAzureCredential()
    client = CosmosClient(COSMOS_ENDPOINT, credential=cred)
    db = client.get_database_client(COSMOS_DATABASE)
    container = db.get_container_client("emails")
    
    # Get all documents
    docs = list(container.query_items(
        "SELECT c.id, c.status, c._rid FROM c",
        enable_cross_partition_query=True
    ))
    
    print(f"\nFound {len(docs)} documents in emails container")
    
    # Categorize documents
    orphans = []
    valid = []
    for doc in docs:
        status = doc.get("status")
        if status is None or status == "":
            orphans.append(doc)
        else:
            valid.append(doc)
    
    print(f"  Valid documents (with status): {len(valid)}")
    print(f"  Orphan documents (no status): {len(orphans)}")
    
    if not orphans:
        print("\nNo orphan documents to clean up!")
        return
    
    # Show orphans
    print("\nOrphan documents:")
    for doc in orphans:
        print(f"  - {doc['id'][:50]}...")
    
    # Try to delete orphans with various partition key attempts
    print("\nAttempting to delete orphan documents...")
    
    # List of possible partition key values to try
    pk_attempts = ["", None, "received", "classified", "needs_review", "discarded", "undefined"]
    
    deleted = 0
    for doc in orphans:
        doc_id = doc["id"]
        success = False
        
        for pk in pk_attempts:
            try:
                if pk is None:
                    # Skip None for now - Cosmos SDK doesn't like it
                    continue
                container.delete_item(item=doc_id, partition_key=pk)
                print(f"  ✅ Deleted {doc_id[:40]}... (pk='{pk}')")
                deleted += 1
                success = True
                break
            except CosmosResourceNotFoundError:
                continue
            except Exception as e:
                if "NotFound" in str(e):
                    continue
                print(f"  ❌ Error for {doc_id[:40]}: {str(e)[:50]}")
                break
        
        if not success:
            print(f"  ⚠️ Could not delete {doc_id[:40]}... (tried all partition keys)")
    
    print(f"\n{deleted} orphan documents deleted")
    
    # Verify
    remaining = list(container.query_items(
        "SELECT c.id FROM c WHERE NOT IS_DEFINED(c.status) OR c.status = null",
        enable_cross_partition_query=True
    ))
    print(f"Remaining orphan documents: {len(remaining)}")

if __name__ == "__main__":
    main()
