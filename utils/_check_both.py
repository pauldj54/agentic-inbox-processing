"""Quick check: compare intake-records vs emails containers."""
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

cred = DefaultAzureCredential()
client = CosmosClient("https://cosmos-docproc-dev-izr2ch55woa3c.documents.azure.com:443/", cred)
db = client.get_database_client("email-processing")

# Check intake-records
c1 = db.get_container_client("intake-records")
items1 = list(c1.query_items(
    "SELECT c.id, c.intakeSource, c.subject, c.status, c.partitionKey, c.receivedAt FROM c",
    enable_cross_partition_query=True,
))
print(f"=== intake-records: {len(items1)} docs ===")
for i in sorted(items1, key=lambda x: x.get("receivedAt", ""), reverse=True)[:10]:
    src = i.get("intakeSource", "?")
    st = i.get("status", "?")
    subj = str(i.get("subject", ""))[:50]
    pk = i.get("partitionKey", "?")
    ra = i.get("receivedAt", "?")
    print(f"  [{src}] status={st}  pk={pk}  recv={ra}  subj={subj}")

print()

# Check old emails container
c2 = db.get_container_client("emails")
items2 = list(c2.query_items(
    "SELECT c.id, c.status, c.subject, c.sender, c.receivedAt FROM c",
    enable_cross_partition_query=True,
))
print(f"=== emails (OLD): {len(items2)} docs ===")
for i in sorted(items2, key=lambda x: x.get("receivedAt", ""), reverse=True)[:10]:
    st = i.get("status", "?")
    subj = str(i.get("subject", ""))[:50]
    sender = i.get("sender", "?")
    ra = i.get("receivedAt", "?")
    print(f"  status={st}  sender={sender}  recv={ra}  subj={subj}")
