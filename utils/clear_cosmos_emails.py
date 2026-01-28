#!/usr/bin/env python3
"""
Clear Cosmos DB Email Documents
===============================
Deletes all documents from the emails container in Cosmos DB.

Usage:
    python utils/clear_cosmos_emails.py [--dry-run] [--all-containers]

Options:
    --dry-run         Show what would be deleted without actually deleting
    --all-containers  Also clear audit-logs, classifications, and extracted-data
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

# Container names
CONTAINER_EMAILS = "emails"
CONTAINER_PE_EVENTS = "pe-events"
CONTAINER_AUDIT_LOGS = "audit-logs"
CONTAINER_CLASSIFICATIONS = "classifications"
CONTAINER_EXTRACTED_DATA = "extracted-data"

ALL_CONTAINERS = [
    CONTAINER_EMAILS,
    CONTAINER_PE_EVENTS,
    CONTAINER_AUDIT_LOGS,
    CONTAINER_CLASSIFICATIONS,
    CONTAINER_EXTRACTED_DATA,
]


def clear_container(database, container_name: str, dry_run: bool = False) -> int:
    """
    Delete all documents from a container.
    
    Args:
        database: Cosmos DB database client
        container_name: Name of the container to clear
        dry_run: If True, only count documents without deleting
        
    Returns:
        Number of documents deleted/counted
    """
    count = 0
    
    try:
        container = database.get_container_client(container_name)
        
        # Query all documents
        query = "SELECT c.id, c.status FROM c"
        items = list(container.query_items(
            query=query,
            enable_cross_partition_query=True
        ))
        
        if dry_run:
            count = len(items)
            print(f"  📋 {container_name}: {count} documents would be deleted")
        else:
            for item in items:
                try:
                    # Try to delete with status as partition key first
                    partition_key = item.get("status", item["id"])
                    container.delete_item(
                        item=item["id"],
                        partition_key=partition_key
                    )
                    count += 1
                except Exception as e:
                    # If that fails, try with id as partition key
                    try:
                        container.delete_item(
                            item=item["id"],
                            partition_key=item["id"]
                        )
                        count += 1
                    except Exception as e2:
                        print(f"    ⚠️  Failed to delete {item['id']}: {e2}")
            
            print(f"  🗑️  {container_name}: {count} documents deleted")
                
    except Exception as e:
        if "NotFound" in str(e):
            print(f"  ⚠️  {container_name}: Container not found (skipped)")
        else:
            print(f"  ❌ {container_name}: Error - {e}")
        
    return count


def main():
    parser = argparse.ArgumentParser(
        description="Delete all documents from Cosmos DB containers"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting"
    )
    parser.add_argument(
        "--all-containers",
        action="store_true",
        help="Also clear audit-logs, classifications, and extracted-data"
    )
    parser.add_argument(
        "--container",
        type=str,
        help="Clear only a specific container"
    )
    args = parser.parse_args()
    
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    database_name = os.environ.get("COSMOS_DATABASE", "email-processing")
    
    if not endpoint:
        print("❌ COSMOS_ENDPOINT not configured")
        sys.exit(1)
    
    print("=" * 60)
    if args.dry_run:
        print("Cosmos DB Document Cleanup (DRY RUN)")
    else:
        print("Cosmos DB Document Cleanup")
    print("=" * 60)
    print(f"Endpoint: {endpoint}")
    print(f"Database: {database_name}")
    print()
    
    # Determine which containers to clear
    if args.container:
        containers_to_clear = [args.container]
    elif args.all_containers:
        containers_to_clear = ALL_CONTAINERS
    else:
        containers_to_clear = [CONTAINER_EMAILS]
    
    # Confirm if not dry run
    if not args.dry_run:
        print(f"⚠️  This will DELETE all documents from: {', '.join(containers_to_clear)}")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() != "yes":
            print("Cancelled.")
            sys.exit(0)
        print()
    
    try:
        from azure.identity import DefaultAzureCredential
        from azure.cosmos import CosmosClient
        
        credential = DefaultAzureCredential()
        client = CosmosClient(endpoint, credential=credential)
        database = client.get_database_client(database_name)
        
        total_deleted = 0
        
        for container_name in containers_to_clear:
            count = clear_container(database, container_name, args.dry_run)
            total_deleted += count
        
        print()
        print("=" * 60)
        if args.dry_run:
            print(f"Total documents that would be deleted: {total_deleted}")
        else:
            print(f"✅ Total documents deleted: {total_deleted}")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
