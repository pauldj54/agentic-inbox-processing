"""
Cleanup orphaned SFTP records from Cosmos DB.

Due to a bug where the agent used `fileId` (sftp-{guid}) instead of `dedupKey`
(base64 file path) for the Cosmos lookup, duplicate records were created:
  - Original record (from Logic App): id = dedupKey, intakeSource = "sftp"
  - Orphan record (from agent fallback): id = sftp-{guid}, intakeSource = "email"

This script finds and deletes the orphan records.

Usage:
    python utils/cleanup_sftp_orphans.py             # Dry run (list orphans)
    python utils/cleanup_sftp_orphans.py --delete     # Delete orphans
"""

import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient

# Load environment
env_path = Path(__file__).parent.parent / ".env01"
load_dotenv(env_path)

COSMOS_ENDPOINT = os.environ["COSMOS_ENDPOINT"]
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "email-processing")
CONTAINER_NAME = "intake-records"


def find_orphans(container):
    """Find orphan records: id starts with 'sftp-' but intakeSource != 'sftp'."""
    query = (
        "SELECT c.id, c.partitionKey, c.status, c.intakeSource, "
        "c.receivedAt, c.classification.category, c.pipelineMode "
        "FROM c WHERE STARTSWITH(c.id, 'sftp-') AND c.intakeSource != 'sftp'"
    )
    return list(container.query_items(
        query=query,
        enable_cross_partition_query=True,
    ))


def delete_orphans(container, orphans):
    """Delete orphan records from Cosmos DB."""
    deleted = 0
    failed = 0
    for doc in orphans:
        try:
            container.delete_item(
                item=doc["id"],
                partition_key=doc["partitionKey"],
            )
            print(f"  Deleted: {doc['id'][:40]}...")
            deleted += 1
        except Exception as e:
            print(f"  FAILED: {doc['id'][:40]}... — {e}")
            failed += 1
    return deleted, failed


def main():
    parser = argparse.ArgumentParser(description="Cleanup orphaned SFTP records from Cosmos DB")
    parser.add_argument("--delete", action="store_true", help="Actually delete orphans (default: dry run)")
    args = parser.parse_args()

    credential = DefaultAzureCredential()
    client = CosmosClient(COSMOS_ENDPOINT, credential=credential)
    database = client.get_database_client(COSMOS_DATABASE)
    container = database.get_container_client(CONTAINER_NAME)

    print(f"Scanning {CONTAINER_NAME} for orphaned SFTP records...")
    orphans = find_orphans(container)
    print(f"Found {len(orphans)} orphan(s).\n")

    if not orphans:
        print("No orphans found. Nothing to do.")
        return

    for doc in orphans:
        print(f"  id={doc['id'][:50]}")
        print(f"    intakeSource={doc.get('intakeSource')} | status={doc.get('status')} | "
              f"category={doc.get('category')} | mode={doc.get('pipelineMode')}")

    if args.delete:
        print(f"\nDeleting {len(orphans)} orphan(s)...")
        deleted, failed = delete_orphans(container, orphans)
        print(f"\nDone. Deleted: {deleted}, Failed: {failed}")
    else:
        print(f"\nDry run — no records deleted. Run with --delete to remove them.")


if __name__ == "__main__":
    main()
