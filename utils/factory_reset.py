#!/usr/bin/env python3
"""
Factory Reset — Application Data
================================
Deletes all documents from active Cosmos DB containers and purges Service Bus
queues to reset the dashboard and processing backlog. By default it pauses the
Logic Apps and Web App before deletion, clears blob attachments, then starts the
apps again so a clean end-to-end test can begin.

Active containers:
  - intake-records  (PK: /partitionKey)
  - pe-events       (PK: /eventType)
  - audit-logs      (PK: /action)
  - classifications (PK: /eventType)

Usage:
    python utils/factory_reset.py              # interactive confirmation
    python utils/factory_reset.py --dry-run    # preview only
    python utils/factory_reset.py --yes        # skip confirmation
    python utils/factory_reset.py --skip-queues # only reset Cosmos DB
    python utils/factory_reset.py --leave-stopped # reset data but keep apps stopped
"""

import os
import sys
import argparse
import shutil
import subprocess
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
from azure.cosmos.partition_key import NonePartitionKeyValue
from azure.servicebus import ServiceBusClient
from azure.storage.blob import BlobServiceClient

from purge_queues import QUEUE_NAMES, purge_queue

# Container name → partition key field
CONTAINERS = {
    "intake-records": "partitionKey",
    "pe-events": "eventType",
    "audit-logs": "action",
    "classifications": "eventType",
}

MAX_DELETE_PASSES = 3
DEFAULT_STORAGE_CONTAINER = "attachments"


def resolve_az_cli() -> str | None:
    """Return the Azure CLI executable path for this environment."""
    return shutil.which("az") or shutil.which("az.cmd")


def env_value(name: str, default: str | None = None) -> str | None:
    """Read an environment value loaded from .env01, stripping optional quotes."""
    value = os.environ.get(name, default)
    return value.strip().strip('"').strip("'") if isinstance(value, str) else value


def run_az(args: list[str], dry_run: bool) -> bool:
    """Run an Azure CLI command and return whether it succeeded."""
    az_cli = resolve_az_cli()
    printable = " ".join(["az", *args])
    if dry_run:
        print(f"  📋 would run: {printable}")
        return True

    if not az_cli:
        print("  ❌ Azure CLI executable not found in PATH for this Python process.")
        print("     Open a new terminal after installing Azure CLI, or run with --skip-app-control to reset data without pausing apps.")
        return False

    command = [az_cli, *args]
    if os.name == "nt" and az_cli.lower().endswith((".cmd", ".bat")):
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/c", az_cli, *args]

    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as e:
        print(f"  ❌ Azure CLI command could not be started: {e}")
        print("     Run `az --version` in this terminal, then retry. Use --skip-app-control if you only want to clear data.")
        return False

    if result.returncode == 0:
        return True

    print(f"  ❌ failed: {printable}")
    if result.stderr.strip():
        print(f"     {result.stderr.strip()}")
    elif result.stdout.strip():
        print(f"     {result.stdout.strip()}")
    return False


def set_logic_app_state(name: str, state: str, subscription: str, resource_group: str, dry_run: bool) -> bool:
    """Enable or disable a Logic App workflow."""
    print(f"  {'📋 would set' if dry_run else '↕️ '} Logic App {name}: {state}")
    return run_az([
        "logic", "workflow", "update",
        "--subscription", subscription,
        "--resource-group", resource_group,
        "--name", name,
        "--state", state,
        "-o", "none",
    ], dry_run)


def set_web_app_state(name: str, action: str, subscription: str, resource_group: str, dry_run: bool) -> bool:
    """Start or stop the Web App."""
    print(f"  {'📋 would' if dry_run else '↕️ '} {action} Web App {name}")
    return run_az([
        "webapp", action,
        "--subscription", subscription,
        "--resource-group", resource_group,
        "--name", name,
        "-o", "none",
    ], dry_run)


def pause_apps(dry_run: bool) -> bool:
    """Pause ingestion and processing resources before data deletion."""
    subscription = env_value("AZURE_SUBSCRIPTION_ID")
    resource_group = env_value("AZURE_RESOURCE_GROUP")
    email_logic_app = env_value("EMAIL_LOGIC_APP_NAME")
    sftp_logic_app = env_value("SFTP_LOGIC_APP_NAME")
    web_app = env_value("WEB_APP_NAME")

    missing = [
        name for name, value in {
            "AZURE_SUBSCRIPTION_ID": subscription,
            "AZURE_RESOURCE_GROUP": resource_group,
            "EMAIL_LOGIC_APP_NAME": email_logic_app,
            "SFTP_LOGIC_APP_NAME": sftp_logic_app,
            "WEB_APP_NAME": web_app,
        }.items()
        if not value
    ]
    if missing:
        print(f"  ❌ app pause skipped; missing config: {', '.join(missing)}")
        return False

    print()
    print("=" * 60)
    print("Pause Azure Apps" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)
    results = [
        set_logic_app_state(email_logic_app, "Disabled", subscription, resource_group, dry_run),
        set_logic_app_state(sftp_logic_app, "Disabled", subscription, resource_group, dry_run),
        set_web_app_state(web_app, "stop", subscription, resource_group, dry_run),
    ]
    return all(results)


def start_apps(dry_run: bool) -> bool:
    """Start processing first, then resume ingestion resources."""
    subscription = env_value("AZURE_SUBSCRIPTION_ID")
    resource_group = env_value("AZURE_RESOURCE_GROUP")
    email_logic_app = env_value("EMAIL_LOGIC_APP_NAME")
    sftp_logic_app = env_value("SFTP_LOGIC_APP_NAME")
    web_app = env_value("WEB_APP_NAME")

    missing = [
        name for name, value in {
            "AZURE_SUBSCRIPTION_ID": subscription,
            "AZURE_RESOURCE_GROUP": resource_group,
            "EMAIL_LOGIC_APP_NAME": email_logic_app,
            "SFTP_LOGIC_APP_NAME": sftp_logic_app,
            "WEB_APP_NAME": web_app,
        }.items()
        if not value
    ]
    if missing:
        print(f"  ❌ app start skipped; missing config: {', '.join(missing)}")
        return False

    print()
    print("=" * 60)
    print("Start Azure Apps" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)
    results = [
        set_web_app_state(web_app, "start", subscription, resource_group, dry_run),
        set_logic_app_state(email_logic_app, "Enabled", subscription, resource_group, dry_run),
        set_logic_app_state(sftp_logic_app, "Enabled", subscription, resource_group, dry_run),
    ]
    return all(results)


def clear_container(database, name: str, pk_field: str, dry_run: bool) -> int:
    """Delete every document from *name*, using *pk_field* for the partition key."""
    try:
        container = database.get_container_client(name)
    except Exception as e:
        if "NotFound" in str(e):
            print(f"  ⚠️  {name}: container not found (skipped)")
        else:
            print(f"  ❌ {name}: {e}")
        return 0

    items = list_container_items(container)

    if dry_run:
        print(f"  📋 {name}: {len(items)} documents would be deleted")
        return len(items)

    deleted = 0
    for pass_number in range(1, MAX_DELETE_PASSES + 1):
        if not items:
            break

        if pass_number > 1:
            print(f"    retry pass {pass_number}: {len(items)} document(s) still present")

        pass_deleted = 0
        for item in items:
            if delete_item(container, item, pk_field):
                pass_deleted += 1

        deleted += pass_deleted
        items = list_container_items(container)

        if pass_deleted == 0:
            break

    print(f"  🗑️  {name}: {deleted} documents deleted")
    if items:
        print(f"  ⚠️  {name}: {len(items)} document(s) still remain after {MAX_DELETE_PASSES} pass(es)")
    return deleted


def list_container_items(container) -> list[dict]:
    """Return all documents in a container across partitions."""
    return list(
        container.query_items(
            query="SELECT * FROM c",
            enable_cross_partition_query=True,
        )
    )


def delete_item(container, item: dict, pk_field: str) -> bool:
    """Delete a Cosmos item, including legacy records with missing partition keys."""
    item_id = item["id"]
    partition_key_candidates = []

    if pk_field in item:
        partition_key_candidates.append(item.get(pk_field))

    partition_key_candidates.extend([
        NonePartitionKeyValue,
        item.get("partitionKey"),
        item_id,
        item.get("emailId"),
        item.get("eventType"),
        item.get("category"),
    ])

    seen = set()
    for partition_key in partition_key_candidates:
        marker = repr(partition_key)
        if marker in seen:
            continue
        seen.add(marker)
        try:
            container.delete_item(item=item_id, partition_key=partition_key)
            return True
        except Exception as e:
            if not is_partition_key_miss(e):
                print(f"    ⚠️  failed to delete {item_id}: {e}")
                return False

    print(f"    ⚠️  failed to delete {item_id}: unable to resolve partition key")
    return False


def is_partition_key_miss(error: Exception) -> bool:
    """Return True when retrying with another partition key candidate may help."""
    message = str(error)
    return any(
        fragment in message
        for fragment in [
            "Entity with the specified id does not exist",
            "Resource Not Found",
            "NotFound",
            "404",
            "PartitionKey",
            "partition key",
        ]
    )


def purge_service_bus_queues(credential, dry_run: bool) -> int:
    """Purge all workflow Service Bus queues using the shared purge utility."""
    namespace = os.environ.get("SERVICEBUS_NAMESPACE")
    if not namespace:
        print("  ❌ SERVICEBUS_NAMESPACE not configured; queues not purged")
        return 0

    print()
    print("=" * 60)
    print("Service Bus Queue Purge" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)
    print(f"Namespace: {namespace}")
    print(f"Queues:    {', '.join(QUEUE_NAMES)}")
    print()

    total = 0
    client = ServiceBusClient(
        fully_qualified_namespace=f"{namespace}.servicebus.windows.net",
        credential=credential,
    )

    with client:
        for queue_name in QUEUE_NAMES:
            total += purge_queue(client, queue_name, dry_run)

    return total


def clear_storage_container(credential, container_name: str, dry_run: bool) -> int:
    """Delete every blob from a storage container."""
    storage_url = env_value("STORAGE_ACCOUNT_URL")
    if not storage_url:
        print("  ❌ STORAGE_ACCOUNT_URL not configured; storage container not cleared")
        return 0

    print()
    print("=" * 60)
    print(f"Storage Container Cleanup ({container_name})" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 60)

    service_client = BlobServiceClient(account_url=storage_url, credential=credential)
    container_client = service_client.get_container_client(container_name)

    try:
        blobs = list(container_client.list_blobs())
    except Exception as e:
        if "ContainerNotFound" in str(e) or "The specified container does not exist" in str(e):
            print(f"  ⚠️  {container_name}: container not found (skipped)")
        else:
            print(f"  ❌ {container_name}: {e}")
        return 0

    if dry_run:
        print(f"  📋 {container_name}: {len(blobs)} blob(s) would be deleted")
        return len(blobs)

    deleted = 0
    for blob in blobs:
        try:
            container_client.delete_blob(blob.name, delete_snapshots="include")
            deleted += 1
        except Exception as e:
            print(f"    ⚠️  failed to delete blob {blob.name}: {e}")

    print(f"  🗑️  {container_name}: {deleted} blob(s) deleted")
    return deleted


def main():
    parser = argparse.ArgumentParser(description="Factory-reset app data, queues, storage, and app state")
    parser.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--skip-app-control", action="store_true", help="Do not stop/start Logic Apps or Web App")
    parser.add_argument("--skip-queues", action="store_true", help="Do not purge Service Bus queues")
    parser.add_argument("--skip-storage", action="store_true", help="Do not delete blobs from storage")
    parser.add_argument("--leave-stopped", action="store_true", help="Do not restart apps after reset")
    parser.add_argument(
        "--storage-container",
        default=DEFAULT_STORAGE_CONTAINER,
        help=f"Blob container to clear (default: {DEFAULT_STORAGE_CONTAINER})",
    )
    parser.add_argument(
        "--container",
        choices=list(CONTAINERS.keys()),
        help="Reset only a specific container",
    )
    args = parser.parse_args()

    endpoint = env_value("COSMOS_ENDPOINT")
    database_name = env_value("COSMOS_DATABASE", "email-processing")

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
    print(f"Queues:     {'skipped' if args.skip_queues else ', '.join(QUEUE_NAMES)}")
    print(f"Storage:    {'skipped' if args.skip_storage else args.storage_container}")
    print(f"Apps:       {'not controlled' if args.skip_app_control else 'pause before reset, start after reset'}")
    print()

    if not args.dry_run and not args.yes:
        confirm = input("⚠️  This will stop apps, DELETE documents/messages/blobs, then restart apps. Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Cancelled.")
            sys.exit(0)
        print()

    credential = DefaultAzureCredential()

    if not args.skip_app_control:
        if not pause_apps(args.dry_run):
            print("❌ Failed to pause all apps. Aborting reset to avoid race conditions.")
            sys.exit(1)

    total_documents = 0
    total_messages = 0
    total_blobs = 0
    apps_started = False
    reset_failed = False

    try:
        client = CosmosClient(url=endpoint, credential=credential)
        db = client.get_database_client(database_name)

        for name, pk_field in targets.items():
            total_documents += clear_container(db, name, pk_field, args.dry_run)

        if not args.skip_queues:
            total_messages = purge_service_bus_queues(credential, args.dry_run)

        if not args.skip_storage:
            total_blobs = clear_storage_container(credential, args.storage_container, args.dry_run)
    except Exception as e:
        reset_failed = True
        print(f"❌ Factory reset failed: {e}")
    finally:
        if not args.skip_app_control and not args.leave_stopped:
            apps_started = start_apps(args.dry_run)

    if reset_failed:
        print("❌ Reset did not complete successfully.")
        sys.exit(1)

    print()
    print("=" * 60)
    if args.dry_run:
        print(f"Total documents that would be deleted: {total_documents}")
        if not args.skip_queues:
            print(f"Total messages that would be deleted:  {total_messages}")
        if not args.skip_storage:
            print(f"Total blobs that would be deleted:     {total_blobs}")
    else:
        print(f"✅ Total documents deleted: {total_documents}")
        if not args.skip_queues:
            print(f"✅ Total messages deleted:  {total_messages}")
        if not args.skip_storage:
            print(f"✅ Total blobs deleted:     {total_blobs}")
        if not args.skip_app_control:
            print(f"✅ Apps restarted:          {apps_started and not args.leave_stopped}")
    print("=" * 60)


if __name__ == "__main__":
    main()
