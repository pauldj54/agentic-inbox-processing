#!/usr/bin/env python3
"""
Test script to verify Microsoft Graph API access for email attachments.
This script checks:
1. Environment variables are set correctly
2. Authentication works (ClientSecret or DefaultAzureCredential)
3. Can access the target mailbox
4. Can list emails and attachments

Usage:
    python utils/test_graph_api.py [user_email]
    
Example:
    python utils/test_graph_api.py admin@M365x66851375.onmicrosoft.com
"""

import os
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent.parent / ".env01"
load_dotenv(env_path)

import aiohttp


def check_environment():
    """Check if required environment variables are set."""
    print("\n" + "=" * 60)
    print("1. CHECKING ENVIRONMENT VARIABLES")
    print("=" * 60)
    
    # Graph API specific credentials
    graph_client_id = os.environ.get("GRAPH_CLIENT_ID")
    graph_client_secret = os.environ.get("GRAPH_CLIENT_SECRET")
    graph_tenant_id = os.environ.get("GRAPH_TENANT_ID")
    
    print(f"\nGraph API Credentials:")
    print(f"  GRAPH_CLIENT_ID:     {'✅ Set' if graph_client_id else '❌ Not set'}")
    print(f"  GRAPH_CLIENT_SECRET: {'✅ Set' if graph_client_secret else '❌ Not set'}")
    print(f"  GRAPH_TENANT_ID:     {'✅ Set' if graph_tenant_id else '❌ Not set'}")
    
    if graph_client_id and graph_client_secret and graph_tenant_id:
        print("\n✅ Using ClientSecretCredential (App Registration)")
        return "client_secret", graph_client_id, graph_client_secret, graph_tenant_id
    else:
        print("\n⚠️  Graph credentials not fully set. Will try DefaultAzureCredential.")
        print("   This may not work for accessing other users' mailboxes.")
        return "default", None, None, graph_tenant_id


async def get_token_client_secret(client_id: str, client_secret: str, tenant_id: str) -> str:
    """Get access token using client credentials flow."""
    print("\n" + "=" * 60)
    print("2. GETTING ACCESS TOKEN (Client Credentials)")
    print("=" * 60)
    
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(token_url, data=data) as response:
            if response.status == 200:
                result = await response.json()
                token = result.get("access_token")
                print(f"✅ Token acquired successfully!")
                print(f"   Token length: {len(token)} characters")
                print(f"   Token preview: {token[:50]}...")
                return token
            else:
                error = await response.text()
                print(f"❌ Failed to get token: {response.status}")
                print(f"   Error: {error}")
                return None


async def get_token_default_credential() -> str:
    """Get access token using DefaultAzureCredential."""
    print("\n" + "=" * 60)
    print("2. GETTING ACCESS TOKEN (DefaultAzureCredential)")
    print("=" * 60)
    
    try:
        from azure.identity import DefaultAzureCredential
        credential = DefaultAzureCredential()
        token = credential.get_token("https://graph.microsoft.com/.default")
        print(f"✅ Token acquired successfully!")
        print(f"   Token length: {len(token.token)} characters")
        return token.token
    except Exception as e:
        print(f"❌ Failed to get token: {e}")
        return None


async def test_list_users(token: str):
    """Test if we can list users (basic Graph API test)."""
    print("\n" + "=" * 60)
    print("3. TESTING BASIC GRAPH API ACCESS")
    print("=" * 60)
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        # Try to get current user or app info
        async with session.get("https://graph.microsoft.com/v1.0/organization", headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                orgs = data.get("value", [])
                if orgs:
                    print(f"✅ Successfully connected to Graph API")
                    print(f"   Organization: {orgs[0].get('displayName', 'Unknown')}")
            else:
                error = await response.text()
                print(f"⚠️  Could not get organization info: {response.status}")


async def test_mailbox_access(token: str, user_email: str):
    """Test if we can access the specified user's mailbox."""
    print("\n" + "=" * 60)
    print(f"4. TESTING MAILBOX ACCESS: {user_email}")
    print("=" * 60)
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # List recent emails
    url = f"https://graph.microsoft.com/v1.0/users/{user_email}/messages?$top=5&$select=id,subject,from,receivedDateTime,hasAttachments"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                messages = data.get("value", [])
                print(f"✅ Successfully accessed mailbox!")
                print(f"   Found {len(messages)} recent messages:\n")
                
                for i, msg in enumerate(messages, 1):
                    sender = msg.get("from", {}).get("emailAddress", {}).get("address", "Unknown")
                    print(f"   {i}. Subject: {msg.get('subject', 'No subject')[:50]}")
                    print(f"      From: {sender}")
                    print(f"      Has Attachments: {msg.get('hasAttachments', False)}")
                    print(f"      ID: {msg.get('id', 'N/A')[:50]}...")
                    print()
                
                return messages
            elif response.status == 403:
                error = await response.json()
                print(f"❌ ACCESS DENIED (403)")
                print(f"   Error: {error.get('error', {}).get('message', 'Unknown error')}")
                print(f"\n   ⚠️  This usually means the app doesn't have Mail.Read permission.")
                print(f"   Go to Azure Portal → App Registration → API permissions → Add Mail.Read")
                return None
            elif response.status == 404:
                print(f"❌ USER NOT FOUND (404)")
                print(f"   The user '{user_email}' was not found in this tenant.")
                return None
            else:
                error = await response.text()
                print(f"❌ Failed to access mailbox: {response.status}")
                print(f"   Error: {error}")
                return None


async def test_attachment_download(token: str, user_email: str, message_id: str):
    """Test if we can download attachments from a specific email."""
    print("\n" + "=" * 60)
    print("5. TESTING ATTACHMENT DOWNLOAD")
    print("=" * 60)
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # List attachments
    url = f"https://graph.microsoft.com/v1.0/users/{user_email}/messages/{message_id}/attachments"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                attachments = data.get("value", [])
                print(f"✅ Successfully retrieved attachments!")
                print(f"   Found {len(attachments)} attachment(s):\n")
                
                for i, att in enumerate(attachments, 1):
                    print(f"   {i}. Name: {att.get('name', 'Unknown')}")
                    print(f"      Content Type: {att.get('contentType', 'Unknown')}")
                    print(f"      Size: {att.get('size', 0)} bytes")
                    print(f"      ID: {att.get('id', 'N/A')[:50]}...")
                    has_content = bool(att.get('contentBytes'))
                    print(f"      Content included: {'✅ Yes' if has_content else '❌ No (need to download separately)'}")
                    print()
                
                return attachments
            else:
                error = await response.text()
                print(f"❌ Failed to get attachments: {response.status}")
                print(f"   Error: {error[:200]}")
                return None


async def main():
    """Main test function."""
    print("\n" + "=" * 60)
    print("   MICROSOFT GRAPH API - EMAIL ACCESS TEST")
    print("=" * 60)
    
    # Get user email from command line or use default
    user_email = sys.argv[1] if len(sys.argv) > 1 else "admin@M365x66851375.onmicrosoft.com"
    print(f"\nTarget mailbox: {user_email}")
    
    # Step 1: Check environment
    auth_type, client_id, client_secret, tenant_id = check_environment()
    
    # Step 2: Get token
    if auth_type == "client_secret":
        token = await get_token_client_secret(client_id, client_secret, tenant_id)
    else:
        token = await get_token_default_credential()
    
    if not token:
        print("\n❌ Cannot proceed without a valid token.")
        print("\nTo fix this:")
        print("1. Create an App Registration in Azure Portal")
        print("2. Add API Permission: Microsoft Graph → Application → Mail.Read")
        print("3. Grant admin consent")
        print("4. Create a client secret")
        print("5. Add to .env01:")
        print("   GRAPH_CLIENT_ID=<app-id>")
        print("   GRAPH_CLIENT_SECRET=<secret>")
        print("   GRAPH_TENANT_ID=<tenant-id>")
        return
    
    # Step 3: Test basic access
    await test_list_users(token)
    
    # Step 4: Test mailbox access
    messages = await test_mailbox_access(token, user_email)
    
    # Step 5: Test attachment download (if we found messages with attachments)
    if messages:
        for msg in messages:
            if msg.get("hasAttachments"):
                await test_attachment_download(token, user_email, msg["id"])
                break  # Just test the first one
        else:
            print("\n⚠️  No messages with attachments found in recent emails.")
    
    print("\n" + "=" * 60)
    print("   TEST COMPLETE")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
