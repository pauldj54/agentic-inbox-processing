"""
Microsoft Graph API Tools for email attachment handling.
Downloads attachments from M365 using Graph API.
Uses DefaultAzureCredential for passwordless authentication.
"""

import os
import logging
import base64
from typing import Optional, List
from azure.identity import DefaultAzureCredential
import aiohttp

logger = logging.getLogger(__name__)


class GraphAPITools:
    """Tools for interacting with Microsoft Graph API for email attachments."""
    
    GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
    
    def __init__(self):
        """Initialize the Graph API client."""
        self.credential = DefaultAzureCredential()
        self._token_cache = None
    
    def _get_token(self) -> str:
        """Get an access token for Microsoft Graph."""
        token = self.credential.get_token("https://graph.microsoft.com/.default")
        return token.token
    
    async def _get_token_async(self) -> str:
        """Get an access token asynchronously."""
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
        return {
            "user_id": email_body.get("userPrincipalName") or email_body.get("from"),
            "message_id": email_body.get("emailId") or email_body.get("messageId"),
            "has_attachments": email_body.get("hasAttachments", False),
            "attachment_paths": email_body.get("attachmentPaths", [])
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
