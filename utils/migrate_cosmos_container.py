"""
One-time migration script: Copy documents from 'emails' to 'intake-records' container.
Backfills intakeSource='email' on each document. Idempotent — skips existing documents.

Usage:
    python -m utils.migrate_cosmos_container
"""

import os
import sys
import logging
from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient, exceptions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SOURCE_CONTAINER = "emails"
TARGET_CONTAINER = "intake-records"


def migrate():
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    database_name = os.environ.get("COSMOS_DATABASE", "email-processing")

    if not endpoint:
        logger.error("Set COSMOS_ENDPOINT environment variable.")
        sys.exit(1)

    credential = DefaultAzureCredential()
    client = CosmosClient(endpoint, credential=credential)
    database = client.get_database_client(database_name)

    source = database.get_container_client(SOURCE_CONTAINER)
    target = database.get_container_client(TARGET_CONTAINER)

    copied = 0
    skipped = 0
    errors = 0

    logger.info(f"Migrating documents from '{SOURCE_CONTAINER}' to '{TARGET_CONTAINER}'...")

    for doc in source.read_all_items():
        doc_id = doc.get("id", "unknown")
        partition_key = doc.get("status", "received")

        # Check if document already exists in target (idempotency)
        try:
            target.read_item(item=doc_id, partition_key=partition_key)
            skipped += 1
            continue
        except exceptions.CosmosResourceNotFoundError:
            pass

        # Backfill intakeSource
        if "intakeSource" not in doc:
            doc["intakeSource"] = "email"

        # Remove Cosmos system properties that can't be written
        for key in ["_rid", "_self", "_etag", "_attachments", "_ts"]:
            doc.pop(key, None)

        try:
            target.upsert_item(doc)
            copied += 1
            if copied % 50 == 0:
                logger.info(f"  Copied {copied} documents so far...")
        except Exception as e:
            logger.error(f"Failed to copy document {doc_id}: {e}")
            errors += 1

    logger.info(f"Migration complete: {copied} copied, {skipped} skipped (already exist), {errors} errors.")


if __name__ == "__main__":
    migrate()
