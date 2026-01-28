"""
Microsoft Graph Tools for Agent Framework
Provides email reading capabilities via Microsoft Graph API.
"""

import os
import json
import asyncio
import concurrent.futures
from typing import Annotated, Optional
from pydantic import Field
from azure.identity import InteractiveBrowserCredential
from msgraph import GraphServiceClient
from msgraph.generated.users.item.messages.messages_request_builder import MessagesRequestBuilder

# =============================================================================
# Configuration (loaded from environment)
# =============================================================================
ENTRA_CLIENT_ID = os.getenv("ENTRA_CLIENT_ID", "")
ENTRA_TENANT_ID = os.getenv("ENTRA_TENANT_ID", "")

# Graph API scopes for email access
GRAPH_SCOPES = ["Mail.Read", "User.Read"]

# Module-level graph client (initialized once)
_graph_client: Optional[GraphServiceClient] = None
_credential: Optional[InteractiveBrowserCredential] = None


def _get_graph_client() -> GraphServiceClient:
    """Get or create the Microsoft Graph client with interactive auth."""
    global _graph_client, _credential
    
    if _graph_client is not None:
        return _graph_client
    
    if not ENTRA_CLIENT_ID or not ENTRA_TENANT_ID:
        raise ValueError(
            "ENTRA_CLIENT_ID and ENTRA_TENANT_ID must be set in environment. "
            "Create an App Registration in Entra ID with Mail.Read permission."
        )
    
    print(f"[Graph] Initializing client with App: {ENTRA_CLIENT_ID}")
    print(f"[Graph] Tenant: {ENTRA_TENANT_ID}")
    print("[Graph] Browser will open for authentication...")
    
    _credential = InteractiveBrowserCredential(
        client_id=ENTRA_CLIENT_ID,
        tenant_id=ENTRA_TENANT_ID
    )
    
    _graph_client = GraphServiceClient(
        credentials=_credential,
        scopes=GRAPH_SCOPES
    )
    
    print("[Graph] Client initialized successfully!")
    return _graph_client


def _run_async(coro):
    """Run an async coroutine, handling nested event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop, safe to use asyncio.run
        return asyncio.run(coro)
    
    # Already in an async context - run in a new thread with its own loop
    def run_in_new_loop():
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()
    
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(run_in_new_loop)
        return future.result()


async def _fetch_emails_async(top_n: int, user_email: Optional[str]) -> str:
    """Async implementation of email fetching."""
    client = _get_graph_client()
    
    query_params = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
        top=top_n,
        select=["id", "subject", "from", "receivedDateTime", "isRead", "hasAttachments", "bodyPreview"],
        orderby=["receivedDateTime DESC"]
    )
    
    request_config = MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration(
        query_parameters=query_params
    )
    
    try:
        if user_email:
            print(f"[Graph] Fetching emails from: {user_email}")
            messages = await client.users.by_user_id(user_email).messages.get(
                request_configuration=request_config
            )
        else:
            print("[Graph] Fetching emails from authenticated user's mailbox")
            messages = await client.me.messages.get(
                request_configuration=request_config
            )
        
        if not messages or not messages.value:
            return json.dumps({"emails": [], "count": 0, "message": "No emails found"})
        
        email_list = []
        for msg in messages.value:
            sender_email = ""
            sender_name = ""
            if msg.from_ and msg.from_.email_address:
                sender_email = msg.from_.email_address.address or ""
                sender_name = msg.from_.email_address.name or ""
            
            email_list.append({
                "id": msg.id,
                "subject": msg.subject or "(No subject)",
                "sender_name": sender_name,
                "sender_email": sender_email,
                "received": msg.received_date_time.isoformat() if msg.received_date_time else "",
                "is_read": msg.is_read,
                "has_attachments": msg.has_attachments,
                "preview": (msg.body_preview or "")[:200]
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


async def _fetch_email_body_async(email_id: str, user_email: Optional[str]) -> str:
    """Async implementation of email body fetching."""
    client = _get_graph_client()
    
    try:
        if user_email:
            message = await client.users.by_user_id(user_email).messages.by_message_id(email_id).get()
        else:
            message = await client.me.messages.by_message_id(email_id).get()
        
        if not message:
            return json.dumps({"error": "Email not found"})
        
        sender_email = ""
        sender_name = ""
        if message.from_ and message.from_.email_address:
            sender_email = message.from_.email_address.address or ""
            sender_name = message.from_.email_address.name or ""
        
        body_content = ""
        body_type = "text"
        if message.body:
            body_content = message.body.content or ""
            body_type = str(message.body.content_type) if message.body.content_type else "text"
        
        result = {
            "id": message.id,
            "subject": message.subject or "(No subject)",
            "sender_name": sender_name,
            "sender_email": sender_email,
            "received": message.received_date_time.isoformat() if message.received_date_time else "",
            "body_type": body_type,
            "body": body_content,
            "has_attachments": message.has_attachments
        }
        
        print(f"[Graph] Retrieved email body: {message.subject}")
        return json.dumps(result, indent=2)
        
    except Exception as e:
        error_msg = f"Error fetching email body: {str(e)}"
        print(f"[Graph] {error_msg}")
        return json.dumps({"error": error_msg})


# =============================================================================
# Tool Functions (sync wrappers for the agent)
# =============================================================================

def get_emails(
    top_n: Annotated[int, Field(description="Number of recent emails to retrieve (max 50)")] = 10,
    user_email: Annotated[Optional[str], Field(description="Email address of the mailbox to read. If not provided, reads from the authenticated user's mailbox.")] = None
) -> str:
    """
    Retrieve the most recent emails from an Outlook mailbox using Microsoft Graph.
    Returns email metadata including subject, sender, received date, and preview.
    """
    print(f"[Tool] get_emails called: top_n={top_n}, user_email={user_email}")
    top_n = min(top_n, 50)
    return _run_async(_fetch_emails_async(top_n, user_email))


def get_email_body(
    email_id: Annotated[str, Field(description="The ID of the email to retrieve the full body for")],
    user_email: Annotated[Optional[str], Field(description="Email address of the mailbox. If not provided, uses authenticated user's mailbox.")] = None
) -> str:
    """
    Retrieve the full body content of a specific email by its ID.
    """
    print(f"[Tool] get_email_body called: email_id={email_id[:20]}...")
    return _run_async(_fetch_email_body_async(email_id, user_email))
