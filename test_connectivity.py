#!/usr/bin/env python3
"""
Azure Services Connectivity Test
Tests connectivity to all required Azure services without modifying any configuration.
"""

import os
import sys
from pathlib import Path

# Load environment
env_file = Path(__file__).parent / ".env01"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def test_azure_identity():
    """Test Azure CLI authentication."""
    print("\n[1/5] Azure Identity (DefaultAzureCredential)...")
    try:
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()
        token = cred.get_token("https://management.azure.com/.default")
        print(f"  ✅ Authenticated successfully (token expires: {token.expires_on})")
        return True, cred
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        return False, None


def test_cosmos_db(credential):
    """Test Cosmos DB connectivity."""
    print("\n[2/5] Azure Cosmos DB...")
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    database = os.environ.get("COSMOS_DATABASE", "email-processing")
    
    if not endpoint:
        print("  ⚠️  SKIPPED: COSMOS_ENDPOINT not configured")
        return None
    
    try:
        from azure.cosmos import CosmosClient
        client = CosmosClient(endpoint, credential=credential)
        db = client.get_database_client(database)
        props = db.read()
        print(f"  ✅ Connected to database: {props['id']}")
        return True
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        return False


def test_service_bus(credential):
    """Test Service Bus connectivity."""
    print("\n[3/5] Azure Service Bus...")
    namespace = os.environ.get("SERVICEBUS_NAMESPACE")
    
    if not namespace:
        print("  ⚠️  SKIPPED: SERVICEBUS_NAMESPACE not configured")
        return None
    
    try:
        from azure.servicebus import ServiceBusClient
        fqdn = f"{namespace}.servicebus.windows.net"
        client = ServiceBusClient(fqdn, credential=credential)
        
        # Try to get a receiver (validates connection)
        with client:
            receiver = client.get_queue_receiver("email-intake", max_wait_time=1)
            with receiver:
                pass  # Just open and close to test connection
        
        print(f"  ✅ Connected to namespace: {namespace}")
        return True
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        return False


def test_ai_agent_service(credential):
    """Test Azure AI Agent Service connectivity."""
    print("\n[4/5] Azure AI Agent Service...")
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
    
    if not endpoint:
        print("  ⚠️  SKIPPED: AZURE_AI_PROJECT_ENDPOINT not configured")
        return None
    
    try:
        from azure.ai.agents import AgentsClient
        client = AgentsClient(endpoint=endpoint, credential=credential)
        
        # List agents to verify connectivity (empty list is fine)
        agents = list(client.list_agents(limit=1))
        print(f"  ✅ Connected to AI Agent Service")
        print(f"      Endpoint: {endpoint[:60]}...")
        return True
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        return False


def test_document_intelligence(credential):
    """Test Document Intelligence connectivity."""
    print("\n[5/5] Azure Document Intelligence...")
    endpoint = os.environ.get("DOCUMENT_INTELLIGENCE_ENDPOINT")
    
    if not endpoint:
        print("  ⚠️  SKIPPED: DOCUMENT_INTELLIGENCE_ENDPOINT not configured")
        return None
    
    try:
        import requests
        
        # Get token for Document Intelligence
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        
        # Simple GET request to check connectivity
        url = f"{endpoint.rstrip('/')}/documentintelligence/info?api-version=2024-11-30"
        headers = {"Authorization": f"Bearer {token.token}"}
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            print(f"  ✅ Connected to Document Intelligence")
            return True
        else:
            print(f"  ❌ FAILED: HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        return False


def main():
    print("=" * 60)
    print("Azure Services Connectivity Test")
    print("=" * 60)
    
    results = {}
    
    # Test Azure Identity first
    success, credential = test_azure_identity()
    results["Azure Identity"] = success
    
    if not credential:
        print("\n⛔ Cannot proceed without valid Azure credentials.")
        print("   Run: az login")
        sys.exit(1)
    
    # Test all services
    results["Cosmos DB"] = test_cosmos_db(credential)
    results["Service Bus"] = test_service_bus(credential)
    results["AI Agent Service"] = test_ai_agent_service(credential)
    results["Document Intelligence"] = test_document_intelligence(credential)
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    failed = []
    for service, status in results.items():
        if status is True:
            print(f"  ✅ {service}")
        elif status is False:
            print(f"  ❌ {service}")
            failed.append(service)
        else:
            print(f"  ⚠️  {service} (skipped)")
    
    print()
    if failed:
        print(f"❌ {len(failed)} service(s) failed connectivity test:")
        for s in failed:
            print(f"   - {s}")
        sys.exit(1)
    else:
        print("✅ All configured services are reachable!")
        sys.exit(0)


if __name__ == "__main__":
    main()
