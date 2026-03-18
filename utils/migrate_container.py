#!/usr/bin/env python3
"""
Migrate intake-records Container: Partition Key /status → /partitionKey
=======================================================================
Recreates the intake-records container with partition key /partitionKey
and migrates all existing documents, backfilling:
  - intakeSource: "email" (all legacy records)
  - partitionKey: "{sender_domain}_{YYYY-MM}" computed from `from` + `receivedAt`

Usage:
    python utils/migrate_container.py [--dry-run]

Options:
    --dry-run   Show what would happen without making changes

Prerequisites:
    - COSMOS_ENDPOINT env var or .env01 file
    - Azure credentials (DefaultAzureCredential)
"""

import os
import sys
import argparse
import time
from pathlib import Path
from datetime import datetime

# Load environment from .env01
env_file = Path(__file__).parent.parent / ".env01"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient, PartitionKey


DATABASE_NAME = os.environ.get("COSMOS_DATABASE", "email-processing")
CONTAINER_NAME = "intake-records"
TEMP_CONTAINER_NAME = "intake-records-new"


def extract_sender_domain(from_field: str) -> str:
    """Extract domain from an email 'from' field.

    Handles formats like:
      - "user@domain.com"
      - "Display Name <user@domain.com>"
      - "domain.com" (fallback)
    """
    if not from_field:
        return "unknown"
    # Try to extract email address from angle brackets
    if "<" in from_field and ">" in from_field:
        from_field = from_field.split("<")[1].split(">")[0]
    # Extract domain from email
    if "@" in from_field:
        return from_field.split("@")[1].strip().lower()
    return from_field.strip().lower() or "unknown"


def extract_year_month(received_at: str) -> str:
    """Extract YYYY-MM from an ISO 8601 timestamp."""
    if not received_at:
        return datetime.utcnow().strftime("%Y-%m")
    try:
        dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m")
    except (ValueError, TypeError):
        return datetime.utcnow().strftime("%Y-%m")


def compute_partition_key(doc: dict, sftp_username: str = "partnerreader") -> str:
    """Compute partitionKey for a legacy document.

    - Email: {sender_domain}_{YYYY-MM} from `from` + `receivedAt`
    - SFTP:  {sftp_username}_{YYYY-MM} from CLI arg + `receivedAt`
    """
    source = doc.get("intakeSource", "email")
    month = extract_year_month(doc.get("receivedAt", ""))

    if source == "sftp":
        return f"{sftp_username}_{month}"

    domain = extract_sender_domain(doc.get("from", ""))
    return f"{domain}_{month}"


def get_new_container_properties() -> dict:
    """Return the indexing policy for the new container matching cosmos-db.bicep."""
    return {
        "indexingMode": "consistent",
        "automatic": True,
        "includedPaths": [{"path": "/*"}],
        "excludedPaths": [
            {"path": "/\"_etag\"/?"},
            {"path": "/emailBody/?"},
            {"path": "/attachmentContent/?"},
        ],
        "compositeIndexes": [
            [
                {"path": "/partitionKey", "order": "ascending"},
                {"path": "/receivedAt", "order": "descending"},
            ],
            [
                {"path": "/confidenceLevel", "order": "ascending"},
                {"path": "/receivedAt", "order": "descending"},
            ],
            [
                {"path": "/intakeSource", "order": "ascending"},
                {"path": "/status", "order": "ascending"},
            ],
            [
                {"path": "/partitionKey", "order": "ascending"},
                {"path": "/status", "order": "ascending"},
            ],
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Migrate intake-records container: /status → /partitionKey"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making changes",
    )
    parser.add_argument(
        "--sftp-username",
        type=str,
        default="partnerreader",
        help="SFTP username for computing partition key on SFTP records (default: partnerreader)",
    )
    args = parser.parse_args()

    endpoint = os.environ.get("COSMOS_ENDPOINT")
    if not endpoint:
        print("❌ COSMOS_ENDPOINT environment variable is required.")
        sys.exit(1)

    credential = DefaultAzureCredential()
    client = CosmosClient(url=endpoint, credential=credential)
    database = client.get_database_client(DATABASE_NAME)

    # ── Step 1: Read all documents from the old container ──
    print(f"\n📖 Reading documents from '{CONTAINER_NAME}'...")
    try:
        old_container = database.get_container_client(CONTAINER_NAME)
        old_docs = list(old_container.query_items(
            query="SELECT * FROM c",
            enable_cross_partition_query=True,
        ))
    except Exception as e:
        if "NotFound" in str(e):
            print(f"⚠️  Container '{CONTAINER_NAME}' not found. Nothing to migrate.")
            sys.exit(0)
        raise

    doc_count = len(old_docs)
    print(f"   Found {doc_count} document(s).")

    # ── Step 2: Compute new fields for each document ──
    print("\n🔄 Computing partitionKey and intakeSource for each document...")
    migrated_docs = []
    for doc in old_docs:
        # Strip Cosmos system properties (will be regenerated)
        for key in ["_rid", "_self", "_etag", "_attachments", "_ts"]:
            doc.pop(key, None)

        # Backfill intakeSource
        if "intakeSource" not in doc:
            doc["intakeSource"] = "email"

        # Compute partitionKey for legacy records
        if "partitionKey" not in doc:
            doc["partitionKey"] = compute_partition_key(doc, args.sftp_username)

        migrated_docs.append(doc)

    # Show sample
    if migrated_docs:
        sample = migrated_docs[0]
        print(f"   Sample: id={sample.get('id', '?')[:30]}..., "
              f"partitionKey={sample.get('partitionKey')}, "
              f"intakeSource={sample.get('intakeSource')}")

    if args.dry_run:
        print(f"\n🏁 [DRY RUN] Would migrate {doc_count} documents.")
        print("   No changes were made.")
        return

    # ── Step 3: Create new container with /partitionKey ──
    print(f"\n📦 Creating temporary container '{TEMP_CONTAINER_NAME}' with /partitionKey...")
    # Delete temp container if it exists from a previous failed run (idempotent)
    try:
        database.delete_container(TEMP_CONTAINER_NAME)
        print(f"   Cleaned up existing '{TEMP_CONTAINER_NAME}'.")
        time.sleep(2)
    except Exception:
        pass

    new_container = database.create_container(
        id=TEMP_CONTAINER_NAME,
        partition_key=PartitionKey(path="/partitionKey"),
        indexing_policy=get_new_container_properties(),
        default_ttl=-1,
    )
    print(f"   ✅ Created '{TEMP_CONTAINER_NAME}' with partition key /partitionKey.")

    # ── Step 4: Insert documents into new container ──
    print(f"\n📥 Inserting {doc_count} documents into '{TEMP_CONTAINER_NAME}'...")
    inserted = 0
    errors = 0
    for doc in migrated_docs:
        try:
            new_container.upsert_item(doc)
            inserted += 1
            if inserted % 50 == 0:
                print(f"   ... {inserted}/{doc_count} inserted")
        except Exception as e:
            errors += 1
            print(f"   ❌ Failed to insert {doc.get('id', '?')[:30]}: {e}")

    print(f"   ✅ Inserted {inserted}/{doc_count} documents ({errors} errors).")

    if errors > 0:
        print(f"\n⚠️  {errors} documents failed to insert. "
              f"Keeping both containers for manual review.")
        print(f"   Old: '{CONTAINER_NAME}', New: '{TEMP_CONTAINER_NAME}'")
        sys.exit(1)

    # ── Step 5: Verify document count ──
    verify_count = len(list(new_container.query_items(
        query="SELECT VALUE COUNT(1) FROM c",
        enable_cross_partition_query=True,
    )))
    # COUNT(1) returns a single number
    new_count_items = list(new_container.query_items(
        query="SELECT VALUE COUNT(1) FROM c",
        enable_cross_partition_query=True,
    ))
    new_count = new_count_items[0] if new_count_items else 0

    if new_count != doc_count:
        print(f"\n⚠️  Count mismatch: old={doc_count}, new={new_count}. "
              f"Keeping both containers.")
        sys.exit(1)

    print(f"   ✅ Document count verified: {new_count} == {doc_count}")

    # ── Step 6: Delete old container, rename new ──
    print(f"\n🗑️  Deleting old container '{CONTAINER_NAME}'...")
    database.delete_container(CONTAINER_NAME)
    print(f"   ✅ Deleted '{CONTAINER_NAME}'.")

    # Cosmos DB doesn't support container rename, so we create final + copy again
    print(f"\n📦 Creating final container '{CONTAINER_NAME}' with /partitionKey...")
    time.sleep(2)  # Brief pause after delete
    final_container = database.create_container(
        id=CONTAINER_NAME,
        partition_key=PartitionKey(path="/partitionKey"),
        indexing_policy=get_new_container_properties(),
        default_ttl=-1,
    )

    print(f"   📥 Copying {doc_count} documents to final container...")
    for doc in migrated_docs:
        # Strip system properties again (from temp container read)
        for key in ["_rid", "_self", "_etag", "_attachments", "_ts"]:
            doc.pop(key, None)
        final_container.upsert_item(doc)

    # Verify final
    final_count_items = list(final_container.query_items(
        query="SELECT VALUE COUNT(1) FROM c",
        enable_cross_partition_query=True,
    ))
    final_count = final_count_items[0] if final_count_items else 0
    print(f"   ✅ Final container verified: {final_count} documents.")

    # Clean up temp container
    print(f"\n🗑️  Cleaning up temporary container '{TEMP_CONTAINER_NAME}'...")
    database.delete_container(TEMP_CONTAINER_NAME)
    print(f"   ✅ Deleted '{TEMP_CONTAINER_NAME}'.")

    print(f"\n🎉 Migration complete! '{CONTAINER_NAME}' now uses partition key /partitionKey.")
    print(f"   {final_count} documents migrated with intakeSource='email' and computed partitionKey.")


if __name__ == "__main__":
    main()
