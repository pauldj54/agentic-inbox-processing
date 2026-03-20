"""Quick Cosmos DB query utility for disposition verification."""
import json
import sys

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

ENDPOINT = "https://cosmos-docproc-dev-izr2ch55woa3c.documents.azure.com:443/"
DATABASE = "email-processing"
CONTAINER = "intake-records"

query = sys.argv[1] if len(sys.argv) > 1 else "SELECT TOP 5 c.id, c.originalFilename, c.status, c.disposition, c.intakeSource FROM c ORDER BY c._ts DESC"

credential = DefaultAzureCredential()
client = CosmosClient(ENDPOINT, credential)
db = client.get_database_client(DATABASE)
container = db.get_container_client(CONTAINER)

items = list(container.query_items(query=query, enable_cross_partition_query=True))
for item in items:
    print(json.dumps(item, indent=2, default=str))
if not items:
    print("No documents found.")
