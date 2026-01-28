#!/usr/bin/env python
"""Check email documents in Cosmos DB."""
import os
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT", "https://cosmos-docproc-dev-izr2ch55woa3c.documents.azure.com:443/")
COSMOS_DATABASE = os.getenv("COSMOS_DATABASE", "email-processing")

def main():
    credential = DefaultAzureCredential()
    client = CosmosClient(COSMOS_ENDPOINT, credential)
    db = client.get_database_client(COSMOS_DATABASE)
    container = db.get_container_client("emails")
    
    emails = list(container.read_all_items())
    print(f"Found {len(emails)} emails in Cosmos DB")
    print("=" * 60)
    
    for e in emails:
        doc_id = e.get("id", "unknown")
        email_id = e.get("emailId")
        status = e.get("status", "N/A")
        subject = e.get("subject", "N/A")
        sender = e.get("sender", "N/A")
        
        print(f"ID: {doc_id[:60]}...")
        print(f"  emailId: {email_id or 'NULL'}")
        print(f"  Status: {status}")
        print(f"  Subject: {subject[:50] if subject else 'N/A'}")
        print(f"  Sender: {sender}")
        print("-" * 40)

if __name__ == "__main__":
    main()
