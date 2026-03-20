"""
Microsoft Graph API Tools for email attachment handling.
Downloads attachments from M365 using Graph API.

Authentication options (in order of preference):
1. Client credentials (App Registration): Set GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_TENANT_ID
2. DefaultAzureCredential: Falls back to Azure CLI, Managed Identity, etc.

For accessing other users' mailboxes, you need Application permissions (Mail.Read or Mail.ReadBasic.All)
granted via App Registration with admin consent.
"""

import os
import logging
import base64
from typing import Optional, List
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
import aiohttp

logger = logging.getLogger(__name__)


class GraphAPITools:
    """Tools for interacting with Microsoft Graph API for email attachments."""
    
    GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
    
    def __init__(self):
        """
        Initialize the Graph API client.
        
        Uses ClientSecretCredential if GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, and 
        GRAPH_TENANT_ID are set. Otherwise falls back to DefaultAzureCredential.
        """
        # Check for explicit Graph API credentials (App Registration)
        self.client_id = os.environ.get("GRAPH_CLIENT_ID")
        self.client_secret = os.environ.get("GRAPH_CLIENT_SECRET")
        self.tenant_id = os.environ.get("GRAPH_TENANT_ID")
        
        # Fall back to Key Vault if client secret not in env
        if self.client_id and not self.client_secret and self.tenant_id:
            kv_url = os.environ.get("KEY_VAULT_URL")
            if kv_url:
                try:
                    kv_client = SecretClient(vault_url=kv_url, credential=DefaultAzureCredential())
                    self.client_secret = kv_client.get_secret("graph-client-secret").value
                    logger.info("Retrieved GRAPH_CLIENT_SECRET from Key Vault")
                except Exception as e:
                    logger.warning(f"Failed to retrieve graph-client-secret from Key Vault: {e}")
        
        if self.client_id and self.client_secret and self.tenant_id:
            logger.info("Using ClientSecretCredential for Graph API (App Registration)")
            self.credential = ClientSecretCredential(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                client_secret=self.client_secret
            )
            self._use_client_secret = True
        else:
            logger.info("Using DefaultAzureCredential for Graph API")
            self.credential = DefaultAzureCredential()
            self._use_client_secret = False
        
        self._token_cache = None
    
    def _get_token(self) -> str:
        """Get an access token for Microsoft Graph."""
        token = self.credential.get_token("https://graph.microsoft.com/.default")
        return token.token
    
    async def _get_token_async(self) -> str:
        """Get an access token asynchronously."""
        if self._use_client_secret:
            # ClientSecretCredential works synchronously, just call sync method
            return self._get_token()
        else:
            from azure.identity.aio import DefaultAzureCredential as AsyncCredential
            async with AsyncCredential() as credential:
                token = await credential.get_token("https://graph.microsoft.com/.default")
                return token.token
    
    async def get_email_attachments(self, user_id: str, message_id: str) -> List[dict]:
        """
        Get list of attachments for a specific email.
        
        Args:
            user_id: User ID or email address
            message_id: The email message ID from Graph API
            
        Returns:
            List of attachment metadata dictionaries
        """
        token = await self._get_token_async()
        
        url = f"{self.GRAPH_BASE_URL}/users/{user_id}/messages/{message_id}/attachments"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Graph API error: {response.status} - {error_text}")
                    return []
                
                data = await response.json()
                attachments = data.get("value", [])
                
                return [
                    {
                        "id": att.get("id"),
                        "name": att.get("name"),
                        "content_type": att.get("contentType"),
                        "size": att.get("size"),
                        "is_inline": att.get("isInline", False),
                        "@odata.type": att.get("@odata.type")
                    }
                    for att in attachments
                ]
    
    async def download_attachment(
        self,
        user_id: str,
        message_id: str,
        attachment_id: str
    ) -> Optional[dict]:
        """
        Download a specific attachment content.
        
        Args:
            user_id: User ID or email address
            message_id: The email message ID
            attachment_id: The attachment ID
            
        Returns:
            Dictionary with attachment name, content_type, and content_bytes (base64)
        """
        token = await self._get_token_async()
        
        url = f"{self.GRAPH_BASE_URL}/users/{user_id}/messages/{message_id}/attachments/{attachment_id}"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Graph API error downloading attachment: {response.status} - {error_text}")
                    return None
                
                data = await response.json()
                
                # contentBytes is base64 encoded
                content_bytes_b64 = data.get("contentBytes", "")
                
                return {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "content_type": data.get("contentType"),
                    "size": data.get("size"),
                    "content_bytes": content_bytes_b64,  # Base64 encoded
                    "content_decoded": base64.b64decode(content_bytes_b64) if content_bytes_b64 else None
                }
    
    async def download_all_pdf_attachments(
        self,
        user_id: str,
        message_id: str
    ) -> List[dict]:
        """
        Download all PDF attachments from an email.
        
        Args:
            user_id: User ID or email address
            message_id: The email message ID
            
        Returns:
            List of dictionaries with attachment content
        """
        attachments = await self.get_email_attachments(user_id, message_id)
        
        pdf_attachments = []
        for att in attachments:
            # Filter for PDF files
            if att.get("content_type") == "application/pdf" or att.get("name", "").lower().endswith(".pdf"):
                content = await self.download_attachment(user_id, message_id, att["id"])
                if content:
                    pdf_attachments.append(content)
                    logger.info(f"Downloaded PDF attachment: {content['name']}")
        
        return pdf_attachments
    
    def extract_email_info_from_message(self, email_body: dict) -> dict:
        """
        Extract Graph API identifiers from the email message body.
        The Logic App should include the message ID and user info.
        
        Args:
            email_body: Email data from the queue message
            
        Returns:
            Dictionary with user_id, message_id, and other identifiers
        """
        # Pass through attachmentPaths unchanged — may be string[] (legacy) or object[] (new)
        raw_paths = email_body.get("attachmentPaths", [])
        # Normalize legacy string entries to object format for downstream consumers
        normalized_paths = []
        for entry in raw_paths:
            if isinstance(entry, str):
                normalized_paths.append({"path": entry, "source": "attachment"})
            else:
                normalized_paths.append(entry)

        return {
            "user_id": email_body.get("userPrincipalName") or email_body.get("from"),
            "message_id": email_body.get("emailId") or email_body.get("messageId"),
            "has_attachments": email_body.get("hasAttachments", False),
            "attachment_paths": normalized_paths
        }


# Tool function definitions for agent framework
def get_graph_tool_definitions() -> list:
    """
    Returns the tool definitions for the Azure AI Agent framework.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "get_email_attachments",
                "description": (
                    "Retrieves the list of attachments for a specific email from Microsoft 365. "
                    "Returns metadata about each attachment including name, size, and content type."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "Email address or user ID of the mailbox owner"
                        },
                        "message_id": {
                            "type": "string",
                            "description": "The Graph API message ID for the email"
                        }
                    },
                    "required": ["user_id", "message_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "download_pdf_attachments",
                "description": (
                    "Downloads all PDF attachments from a specific email. "
                    "Returns the binary content of each PDF file for processing "
                    "with Document Intelligence."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "Email address or user ID of the mailbox owner"
                        },
                        "message_id": {
                            "type": "string",
                            "description": "The Graph API message ID for the email"
                        }
                    },
                    "required": ["user_id", "message_id"]
                }
            }
        }
    ]
