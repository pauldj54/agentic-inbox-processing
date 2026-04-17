#!/usr/bin/env python3
"""
Factory Reset — Cosmos DB
=========================
Deletes all documents from active Cosmos DB containers to reset the dashboard.

Active containers:
  - intake-records  (PK: /partitionKey)
  - pe-events       (PK: /eventType)
  - audit-logs      (PK: /action)
  - classifications (PK: /eventType)

Usage:
    python utils/factory_reset.py              # interactive confirmation
    python utils/factory_reset.py --dry-run    # preview only
    python utils/factory_reset.py --yes        # skip confirmation
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

from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient

# Container name → partition key field
CONTAINERS = {
    "intake-records": "partitionKey",
    "pe-events": "eventType",
    "audit-logs": "action",
    "classifications": "eventType",
}


def clear_container(database, name: str, pk_field: str, dry_run: bool) -> int:
    """Delete every document from *name*, using *pk_field* for the partition key."""
    try:
        container = database.get_container_client(name)
        items = list(
            container.query_items(
                query=f"SELECT c.id, c.{pk_field} FROM c",
                enable_cross_partition_query=True,
            )
        )
    except Exception as e:
        if "NotFound" in str(e):
            print(f"  ⚠️  {name}: container not found (skipped)")
        else:
            print(f"  ❌ {name}: {e}")
        return 0

    if dry_run:
        print(f"  📋 {name}: {len(items)} documents would be deleted")
        return len(items)

    deleted = 0
    for item in items:
        pk_value = item.get(pk_field, item["id"])
        try:
            container.delete_item(item=item["id"], partition_key=pk_value)
            deleted += 1
        except Exception as e:
            print(f"    ⚠️  failed to delete {item['id']}: {e}")

    print(f"  🗑️  {name}: {deleted} documents deleted")
    return deleted


def main():
    parser = argparse.ArgumentParser(description="Factory-reset Cosmos DB containers")
    parser.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument(
        "--container",
        choices=list(CONTAINERS.keys()),
        help="Reset only a specific container",
    )
    args = parser.parse_args()

    endpoint = os.environ.get("COSMOS_ENDPOINT")
    database_name = os.environ.get("COSMOS_DATABASE", "email-processing")

    if not endpoint:
        print("❌ COSMOS_ENDPOINT not set")
        sys.exit(1)

    targets = {args.container: CONTAINERS[args.container]} if args.container else CONTAINERS

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print("=" * 60)
    print(f"Factory Reset — Cosmos DB ({mode})")
    print("=" * 60)
    print(f"Endpoint:   {endpoint}")
    print(f"Database:   {database_name}")
    print(f"Containers: {', '.join(targets)}")
    print()

    if not args.dry_run and not args.yes:
        confirm = input("⚠️  This will DELETE all documents. Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Cancelled.")
            sys.exit(0)
        print()

    client = CosmosClient(url=endpoint, credential=DefaultAzureCredential())
    db = client.get_database_client(database_name)

    total = 0
    for name, pk_field in targets.items():
        total += clear_container(db, name, pk_field, args.dry_run)

    print()
    print("=" * 60)
    if args.dry_run:
        print(f"Total documents that would be deleted: {total}")
    else:
        print(f"✅ Total documents deleted: {total}")
    print("=" * 60)


if __name__ == "__main__":
    main()
