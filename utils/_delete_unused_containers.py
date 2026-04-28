#!/usr/bin/env python3
"""One-time script to delete unused Cosmos DB containers: emails, fund-mappings."""
import os
from pathlib import Path

env_file = Path(__file__).parent.parent / ".env01"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient

endpoint = os.environ["COSMOS_ENDPOINT"]
client = CosmosClient(url=endpoint, credential=DefaultAzureCredential())
db = client.get_database_client("email-processing")

for name in ["emails", "fund-mappings"]:
    try:
        db.delete_container(name)
        print(f"Deleted container: {name}")
    except Exception as e:
        print(f"Error deleting {name}: {e}")

print("\nRemaining containers:")
for c in db.list_containers():
    print(f"  - {c['id']}")
