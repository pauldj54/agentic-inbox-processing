"""Fix Cosmos DB records where attachmentPaths were flattened by the regex fallback parser.

The bug caused each attachment object (4 properties) to be decomposed into 8 separate entries
(key name + value for each property). This script detects and repairs the flattened arrays.

Usage:
    python utils/fix_flattened_attachments.py          # dry run
    python utils/fix_flattened_attachments.py --apply   # apply fixes
"""

import json
import sys

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

ENDPOINT = "https://cosmos-docproc-dev-izr2ch55woa3c.documents.azure.com:443/"
DATABASE = "email-processing"
CONTAINER = "intake-records"

# Keys that indicate a flattened object entry (these appear as "path" values)
OBJECT_KEYS = {"path", "source", "contentMd5", "originalName"}

apply = "--apply" in sys.argv


def is_flattened(attachment_paths: list) -> bool:
    """Detect if an attachmentPaths array contains flattened key-value entries."""
    if not attachment_paths or len(attachment_paths) < 8:
        return False
    # Check if any entry has a "path" value that matches an object key name
    key_entries = sum(1 for p in attachment_paths if p.get("path") in OBJECT_KEYS)
    # If more than 25% of entries are key names, it's flattened
    return key_entries > len(attachment_paths) * 0.25


def reconstruct(attachment_paths: list) -> list:
    """Reconstruct proper attachment objects from flattened key-value entries.
    
    Flattened pattern (every 8 entries = 1 real attachment):
      [0] {"path": "path", "source": "attachment"}       ← key
      [1] {"path": "<actual_path>", "source": "..."}     ← value
      [2] {"path": "source", "source": "attachment"}      ← key
      [3] {"path": "attachment", "source": "attachment"}   ← value
      [4] {"path": "contentMd5", "source": "attachment"}   ← key
      [5] {"path": "<actual_md5>", "source": "..."}        ← value
      [6] {"path": "originalName", "source": "attachment"} ← key
      [7] {"path": "<actual_name>", "source": "..."}       ← value
    """
    fixed = []
    i = 0
    while i + 7 < len(attachment_paths):
        # Verify the key pattern
        keys = [attachment_paths[i + j * 2].get("path") for j in range(4)]
        if keys == ["path", "source", "contentMd5", "originalName"]:
            fixed.append({
                "path": attachment_paths[i + 1]["path"],
                "source": attachment_paths[i + 3]["path"],
                "contentMd5": attachment_paths[i + 5]["path"],
                "originalName": attachment_paths[i + 7]["path"],
            })
            i += 8
        else:
            # Not a flattened chunk — keep as-is (shouldn't happen)
            fixed.append(attachment_paths[i])
            i += 1
    # Append any remaining entries (shouldn't happen for properly flattened data)
    while i < len(attachment_paths):
        fixed.append(attachment_paths[i])
        i += 1
    return fixed


credential = DefaultAzureCredential()
client = CosmosClient(ENDPOINT, credential)
db = client.get_database_client(DATABASE)
container = db.get_container_client(CONTAINER)

# Find all records with potentially flattened attachmentPaths
query = "SELECT * FROM c WHERE ARRAY_LENGTH(c.attachmentPaths) > 4"
items = list(container.query_items(query=query, enable_cross_partition_query=True))

fixed_count = 0
for item in items:
    paths = item.get("attachmentPaths", [])
    if not is_flattened(paths):
        continue

    original_count = len(paths)
    fixed_paths = reconstruct(paths)
    new_count = len(fixed_paths)

    print(f"\n{'[FIX]' if apply else '[DRY RUN]'} {item.get('subject', 'N/A')}")
    print(f"  attachmentPaths: {original_count} → {new_count}")
    for fp in fixed_paths:
        print(f"    - {fp.get('originalName', fp.get('path', '?'))}")

    if apply:
        item["attachmentPaths"] = fixed_paths
        item["attachmentsCount"] = new_count
        item["hasAttachments"] = new_count > 0
        container.upsert_item(item)
        print("  ✅ Updated in Cosmos")

    fixed_count += 1

if fixed_count == 0:
    print("No flattened records found.")
else:
    print(f"\n{'Fixed' if apply else 'Would fix'} {fixed_count} record(s).")
    if not apply:
        print("Run with --apply to apply fixes.")
