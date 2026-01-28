"""Clean up PE events from Cosmos DB."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env01'))

from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient

COSMOS_ENDPOINT = "https://cosmos-docproc-dev-izr2ch55woa3c.documents.azure.com:443/"
COSMOS_DATABASE = "email-processing"

cred = DefaultAzureCredential()
client = CosmosClient(COSMOS_ENDPOINT, credential=cred)
db = client.get_database_client(COSMOS_DATABASE)
pe_container = db.get_container_client('pe-events')

items = list(pe_container.query_items('SELECT * FROM c', enable_cross_partition_query=True))
print(f'Found {len(items)} PE events')

for item in items:
    pe_container.delete_item(item=item['id'], partition_key=item.get('eventType'))
    print(f"  Deleted {item['id']}")

print('Done')
