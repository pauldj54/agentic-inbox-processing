"""
Microsoft Graph Tools for Agent Framework
Provides email reading capabilities via Microsoft Graph API.
Uses synchronous HTTP requests to avoid async event loop issues.
"""

import os
import json
from typing import Annotated, Optional
from pydantic import Field
from dotenv import load_dotenv
import requests
from azure.identity import DeviceCodeCredential, TokenCachePersistenceOptions

# Load environment variables
load_dotenv('.env01')

# =============================================================================
# Configuration (loaded from environment)
# =============================================================================
ENTRA_CLIENT_ID = os.getenv("ENTRA_CLIENT_ID", "")
ENTRA_TENANT_ID = os.getenv("ENTRA_TENANT_ID", "")

# Graph API base URL
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

# Graph API scopes for email access
GRAPH_SCOPES = ["https://graph.microsoft.com/Mail.Read", "https://graph.microsoft.com/User.Read"]

# Cache the credential
_credential: Optional[DeviceCodeCredential] = None


def _get_credential() -> DeviceCodeCredential:
    """Get or create the credential."""
    global _credential
    
    if _credential is not None:
        return _credential
    
    if not ENTRA_CLIENT_ID or not ENTRA_TENANT_ID:
        raise ValueError(
            f"ENTRA_CLIENT_ID and ENTRA_TENANT_ID must be set in .env01. "
            f"Current values: CLIENT_ID='{ENTRA_CLIENT_ID}', TENANT_ID='{ENTRA_TENANT_ID}'"
        )
    
    print(f"[Graph] Initializing credential with App: {ENTRA_CLIENT_ID}")
    print(f"[Graph] Tenant: {ENTRA_TENANT_ID}")
    
    # Enable persistent token cache to avoid repeated logins
    cache_options = TokenCachePersistenceOptions(
        name="inbox_agent_cache",
        allow_unencrypted_storage=True
    )
    
    _credential = DeviceCodeCredential(
        client_id=ENTRA_CLIENT_ID,
        tenant_id=ENTRA_TENANT_ID,
        cache_persistence_options=cache_options
    )
    
    print("[Graph] Credential initialized!")
    return _credential


def _get_access_token() -> str:
    """Get an access token for Graph API."""
    credential = _get_credential()
    token = credential.get_token("https://graph.microsoft.com/.default")
    return token.token


def _make_graph_request(endpoint: str, method: str = "GET") -> dict:
    """Make a synchronous request to Microsoft Graph API."""
    token = _get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    url = f"{GRAPH_BASE_URL}{endpoint}"
    
    response = requests.request(method, url, headers=headers, timeout=60)
    
    if response.status_code >= 400:
        error_detail = response.text[:500] if response.text else "No details"
        raise Exception(f"Graph API error {response.status_code}: {error_detail}")
    
    return response.json()


def get_emails(
    top_n: Annotated[int, Field(description="Number of recent emails to retrieve (max 50)")] = 10,
    user_email: Annotated[Optional[str], Field(description="Email address of the mailbox to read. If not provided, reads from the authenticated user's mailbox.")] = None
) -> str:
    """
    Retrieve the most recent emails from an Outlook mailbox using Microsoft Graph.
    Returns email metadata including subject, sender, received date, and preview.
    """
    print(f"[Tool] get_emails called: top_n={top_n}, user_email={user_email}")
    
    # Cap at 50 to avoid excessive data
    top_n = min(top_n, 50)
    
    try:
        # Build the endpoint
        select_fields = "id,subject,from,receivedDateTime,isRead,hasAttachments,bodyPreview"
        
        if user_email:
            endpoint = f"/users/{user_email}/messages?$top={top_n}&$select={select_fields}&$orderby=receivedDateTime DESC"
            print(f"[Graph] Fetching emails from: {user_email}")
        else:
            endpoint = f"/me/messages?$top={top_n}&$select={select_fields}&$orderby=receivedDateTime DESC"
            print("[Graph] Fetching emails from authenticated user's mailbox")
        
        data = _make_graph_request(endpoint)
        
        messages = data.get("value", [])
        
        if not messages:
            return json.dumps({"emails": [], "count": 0, "message": "No emails found"})
        
        # Format results
        email_list = []
        for msg in messages:
            sender_info = msg.get("from", {}).get("emailAddress", {})
            
            email_list.append({
                "id": msg.get("id"),
                "subject": msg.get("subject") or "(No subject)",
                "sender_name": sender_info.get("name", ""),
                "sender_email": sender_info.get("address", ""),
                "received": msg.get("receivedDateTime", ""),
                "is_read": msg.get("isRead", False),
                "has_attachments": msg.get("hasAttachments", False),
                "preview": (msg.get("bodyPreview") or "")[:200]
            })
        
        print(f"[Graph] Retrieved {len(email_list)} emails")
        
        return json.dumps({
            "emails": email_list,
            "count": len(email_list),
            "mailbox": user_email or "me"
        }, indent=2)
        
    except Exception as e:
        error_msg = f"Error fetching emails: {str(e)}"
        print(f"[Graph] {error_msg}")
        return json.dumps({"error": error_msg})


def get_email_body(
    email_id: Annotated[str, Field(description="The ID of the email to retrieve the full body for")],
    user_email: Annotated[Optional[str], Field(description="Email address of the mailbox. If not provided, uses authenticated user's mailbox.")] = None
) -> str:
    """
    Retrieve the full body content of a specific email by its ID.
    """
    print(f"[Tool] get_email_body called: email_id={email_id[:20]}...")
    
    try:
        if user_email:
            endpoint = f"/users/{user_email}/messages/{email_id}"
        else:
            endpoint = f"/me/messages/{email_id}"
        
        msg = _make_graph_request(endpoint)
        
        if not msg:
            return json.dumps({"error": "Email not found"})
        
        sender_info = msg.get("from", {}).get("emailAddress", {})
        body_info = msg.get("body", {})
        
        result = {
            "id": msg.get("id"),
            "subject": msg.get("subject") or "(No subject)",
            "sender_name": sender_info.get("name", ""),
            "sender_email": sender_info.get("address", ""),
            "received": msg.get("receivedDateTime", ""),
            "body_type": body_info.get("contentType", "text"),
            "body": body_info.get("content", ""),
            "has_attachments": msg.get("hasAttachments", False)
        }
        
        print(f"[Graph] Retrieved email body: {msg.get('subject')}")
        return json.dumps(result, indent=2)
        
    except Exception as e:
        error_msg = f"Error fetching email body: {str(e)}"
        print(f"[Graph] {error_msg}")
        return json.dumps({"error": error_msg})
