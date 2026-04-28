import json, sys
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
ENDPOINT = "https://cosmos-docproc-dev-izr2ch55woa3c.documents.azure.com:443/"
DB = "email-processing"
container_name = sys.argv[1]
query = sys.argv[2]
client = CosmosClient(ENDPOINT, DefaultAzureCredential())
container = client.get_database_client(DB).get_container_client(container_name)
items = list(container.query_items(query=query, enable_cross_partition_query=True))
for it in items: print(json.dumps(it, indent=2, default=str))
print(f"-- {len(items)} item(s)")
