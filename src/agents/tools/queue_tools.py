"""
Service Bus Queue Tools for email processing.
Handles reading from email-intake queue and routing to other queues.
Uses DefaultAzureCredential for passwordless authentication.
"""

import os
import json
import logging
from typing import Optional, List, Union
from datetime import datetime
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient as AsyncServiceBusClient

logger = logging.getLogger(__name__)


def parse_bool(value: Union[str, bool, None]) -> bool:
    """
    Parse a value that might be a string boolean to actual boolean.
    Handles: 'True', 'true', 'FALSE', True, False, None, etc.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1")
    return bool(value) if value is not None else False


class QueueTools:
    """Tools for interacting with Azure Service Bus queues."""
    
    # Queue names for the email processing pipeline (simplified)
    QUEUE_EMAIL_INTAKE = "email-intake"       # Incoming emails
    QUEUE_DISCARDED = "discarded"             # Non-PE emails
    QUEUE_HUMAN_REVIEW = "human-review"       # Low confidence (<65%) needs disambiguation
    QUEUE_ARCHIVAL_PENDING = "archival-pending"  # Ready for archival (>=65% confidence)
    
    def __init__(
        self,
        namespace: Optional[str] = None,
        triage_queue: Optional[str] = None,
        triage_sb_namespace: Optional[str] = None,
    ):
        """
        Initialize the Service Bus client.
        
        Args:
            namespace: Service Bus namespace (without .servicebus.windows.net).
                      If not provided, reads from SERVICEBUS_NAMESPACE env var.
            triage_queue: Queue name for triage-complete output.
                         Defaults to TRIAGE_COMPLETE_QUEUE env var or "triage-complete".
            triage_sb_namespace: Optional external Service Bus namespace for triage queue.
                               Defaults to TRIAGE_COMPLETE_SB_NAMESPACE env var.
        """
        self.namespace = namespace or os.environ.get("SERVICEBUS_NAMESPACE")
        if not self.namespace:
            raise ValueError(
                "Service Bus namespace is required. "
                "Set SERVICEBUS_NAMESPACE environment variable."
            )
        
        self.fully_qualified_namespace = f"{self.namespace}.servicebus.windows.net"
        self.credential = DefaultAzureCredential()

        # Triage-complete queue configuration
        self.triage_queue = triage_queue or os.environ.get("TRIAGE_COMPLETE_QUEUE", "triage-complete")
        self._triage_sb_namespace = triage_sb_namespace or os.environ.get("TRIAGE_COMPLETE_SB_NAMESPACE")
        
    def _get_sync_client(self) -> ServiceBusClient:
        """Get synchronous Service Bus client."""
        return ServiceBusClient(
            fully_qualified_namespace=self.fully_qualified_namespace,
            credential=self.credential
        )
    
    def _get_async_client(self) -> AsyncServiceBusClient:
        """Get asynchronous Service Bus client."""
        from azure.identity.aio import DefaultAzureCredential as AsyncCredential
        return AsyncServiceBusClient(
            fully_qualified_namespace=self.fully_qualified_namespace,
            credential=AsyncCredential()
        )
    
    def receive_email_from_intake(self, max_wait_seconds: int = 30) -> Optional[dict]:
        """
        Receive and complete a single email message from the intake queue.
        
        Args:
            max_wait_seconds: Maximum time to wait for a message
            
        Returns:
            Email message as dictionary, or None if no messages available
        """
        logger.info(f"Receiving message from {self.QUEUE_EMAIL_INTAKE}...")
        
        with self._get_sync_client() as client:
            receiver = client.get_queue_receiver(
                queue_name=self.QUEUE_EMAIL_INTAKE,
                max_wait_time=max_wait_seconds
            )
            
            with receiver:
                messages = receiver.receive_messages(max_message_count=1, max_wait_time=max_wait_seconds)
                
                if not messages:
                    logger.info("No messages in intake queue")
                    return None
                
                msg = messages[0]
                
                try:
                    # Parse message body
                    body_str = str(msg)
                    body = self._parse_message_body(body_str)
                    
                    # Complete the message (remove from queue)
                    receiver.complete_message(msg)
                    
                    logger.info(f"Received and completed message: {msg.sequence_number}")
                    
                    return {
                        "sequence_number": msg.sequence_number,
                        "enqueued_time": msg.enqueued_time_utc.isoformat() if msg.enqueued_time_utc else None,
                        "message_id": msg.message_id,
                        "body": body,
                        "received_at": datetime.utcnow().isoformat()
                    }
                    
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    # Abandon message so it can be retried
                    receiver.abandon_message(msg)
                    raise
    
    def _parse_message_body(self, body_str: str) -> dict:
        """
        Parse message body, handling control characters in JSON.
        
        Args:
            body_str: Raw message body as string
            
        Returns:
            Parsed dictionary
        """
        import re
        
        def normalize_fields(data: dict) -> dict:
            """Normalize hasAttachments to boolean and attachmentsCount to int."""
            # Normalize hasAttachments to boolean
            if "hasAttachments" in data:
                data["hasAttachments"] = parse_bool(data["hasAttachments"])
            # Normalize attachmentsCount to int
            att_count = data.get("attachmentsCount", 0)
            try:
                data["attachmentsCount"] = int(att_count) if att_count else 0
            except (ValueError, TypeError):
                data["attachmentsCount"] = 0
            return data
        
        try:
            # Try direct JSON parse first
            return normalize_fields(json.loads(body_str))
        except json.JSONDecodeError:
            pass
        
        # Fix control characters inside JSON strings
        def fix_json_control_chars(s):
            result = []
            in_string = False
            escape_next = False
            
            for char in s:
                if escape_next:
                    result.append(char)
                    escape_next = False
                elif char == '\\':
                    result.append(char)
                    escape_next = True
                elif char == '"':
                    result.append(char)
                    in_string = not in_string
                elif in_string and ord(char) < 32:
                    if char == '\n':
                        result.append('\\n')
                    elif char == '\r':
                        result.append('\\r')
                    elif char == '\t':
                        result.append('\\t')
                    else:
                        result.append(f'\\u{ord(char):04x}')
                else:
                    result.append(char)
            
            return ''.join(result)
        
        try:
            fixed_json = fix_json_control_chars(body_str)
            return normalize_fields(json.loads(fixed_json))
        except json.JSONDecodeError as e:
            # Fallback: extract key fields via regex
            logger.warning(f"JSON parse failed, using regex extraction: {e}")
            
            email_match = re.search(r'"emailId":\s*"([^"]*)"', body_str)
            from_match = re.search(r'"from":\s*"([^"]*)"', body_str)
            subject_match = re.search(r'"subject":\s*"([^"]*)"', body_str)
            body_text_match = re.search(r'"bodyText":\s*"(.*?)"(?=,\s*"|\})', body_str, re.DOTALL)
            received_at_match = re.search(r'"receivedAt":\s*"([^"]*)"', body_str)
            
            # Extract hasAttachments (handles both boolean True/False and string "True"/"False")
            has_attachments_match = re.search(r'"hasAttachments":\s*(true|false|True|False|"True"|"False")', body_str, re.IGNORECASE)
            has_attachments = False
            if has_attachments_match:
                val = has_attachments_match.group(1).strip('"').lower()
                has_attachments = val == "true"
            
            # Extract attachmentPaths array - this is CRITICAL for PE classification
            # Supports both legacy string format and new object format {path, source}
            attachment_paths = []
            attachment_paths_match = re.search(r'"attachmentPaths":\s*\[(.*?)\]', body_str, re.DOTALL)
            if attachment_paths_match:
                paths_content = attachment_paths_match.group(1).strip()
                if paths_content:
                    # Try to detect object format: look for {"path": ...}
                    object_matches = re.findall(
                        r'\{\s*"path"\s*:\s*"([^"]+)"\s*,\s*"source"\s*:\s*"([^"]+)"\s*\}',
                        paths_content
                    )
                    if object_matches:
                        # New object format: [{"path": "...", "source": "..."}]
                        attachment_paths = [{"path": m[0], "source": m[1]} for m in object_matches]
                    else:
                        # Legacy string format: ["path1", "path2"]
                        path_matches = re.findall(r'"([^"]+)"', paths_content)
                        attachment_paths = [{"path": p, "source": "attachment"} for p in path_matches]
            
            logger.warning(f"Regex extraction: hasAttachments={has_attachments}, attachmentPaths count={len(attachment_paths)}")
            if attachment_paths:
                logger.info(f"Attachment paths found: {attachment_paths}")
            
            return {
                "emailId": email_match.group(1) if email_match else "unknown",
                "from": from_match.group(1) if from_match else "unknown",
                "subject": subject_match.group(1) if subject_match else "unknown",
                "bodyText": body_text_match.group(1)[:500] if body_text_match else "",
                "receivedAt": received_at_match.group(1) if received_at_match else "",
                "hasAttachments": has_attachments,
                "attachmentPaths": attachment_paths,
                "_parse_note": "Extracted via regex due to JSON parsing issues"
            }
    
    def route_email(
        self,
        email_data: dict,
        confidence_score: float,
        classification: str,
        classification_details: dict
    ) -> str:
        """
        Route an email to the appropriate queue based on classification and confidence.
        
        Args:
            email_data: Original email data
            confidence_score: Classification confidence (0.0 to 1.0)
            classification: Category assigned to the email
            classification_details: Additional classification metadata
            
        Returns:
            Name of the queue the message was sent to
        """
        # Handle non-PE emails - route to discarded queue
        if classification == "Not PE Related":
            target_queue = self.QUEUE_DISCARDED
            status = "discarded"
        # Determine target queue based on 65% confidence threshold
        elif confidence_score >= 0.65:
            # ✅ Sufficient confidence - ready for archival
            target_queue = self.QUEUE_ARCHIVAL_PENDING
            status = "classified"
        else:
            # ⚠️ Low confidence (<65%) - needs human disambiguation
            target_queue = self.QUEUE_HUMAN_REVIEW
            status = "needs_review"
        
        # Build the routed message with all key fields at top level for easy display
        routed_message = {
            # Key fields for dashboard display (flattened for easy access)
            "emailId": email_data.get("emailId", "unknown"),
            "from": email_data.get("from", "unknown"),
            "subject": email_data.get("subject", "unknown"),
            "receivedAt": email_data.get("receivedAt", ""),
            "hasAttachments": email_data.get("hasAttachments", False),
            # Classification results
            "category": classification,
            "confidence": confidence_score,
            "fund_name": classification_details.get("fund_name", "Unknown"),
            "pe_company": classification_details.get("pe_company", "Unknown"),
            "status": status,
            # Processing timestamp
            "processedAt": datetime.utcnow().isoformat(),
            # Full details for debugging/audit
            "original_email": email_data,
            "classification_details": classification_details,
            "routing": {
                "source_queue": self.QUEUE_EMAIL_INTAKE,
                "target_queue": target_queue,
                "routed_at": datetime.utcnow().isoformat()
            }
        }
        
        # Send to target queue
        self._send_to_queue(target_queue, routed_message)
        
        logger.info(
            f"Routed email to {target_queue} "
            f"(confidence: {confidence_score:.2%}, category: {classification})"
        )
        
        return target_queue
    
    def _send_to_queue(self, queue_name: str, message_data: dict):
        """
        Send a message to a specific queue.
        
        Args:
            queue_name: Target queue name
            message_data: Data to send as JSON
        """
        with self._get_sync_client() as client:
            sender = client.get_queue_sender(queue_name=queue_name)
            
            with sender:
                message = ServiceBusMessage(
                    body=json.dumps(message_data, default=str),
                    content_type="application/json"
                )
                sender.send_messages(message)

    def _get_triage_sync_client(self) -> ServiceBusClient:
        """Get SB client for the triage queue — external namespace if set, else primary."""
        ns = self._triage_sb_namespace or self.namespace
        fqns = f"{ns}.servicebus.windows.net"
        return ServiceBusClient(
            fully_qualified_namespace=fqns,
            credential=DefaultAzureCredential(),
        )

    def send_to_triage_queue(self, message_data: dict) -> str:
        """
        Send a message to the triage-complete queue.
        Uses the external Service Bus namespace if TRIAGE_COMPLETE_SB_NAMESPACE is set,
        otherwise uses the primary namespace.

        Args:
            message_data: Triage-complete message payload

        Returns:
            The target queue name

        Raises:
            Exception: If the send fails after dead-letter fallback attempt
        """
        try:
            with self._get_triage_sync_client() as client:
                sender = client.get_queue_sender(queue_name=self.triage_queue)
                with sender:
                    msg = ServiceBusMessage(
                        body=json.dumps(message_data, default=str),
                        content_type="application/json",
                    )
                    sender.send_messages(msg)
            logger.info(f"Sent triage-complete message to {self.triage_queue}")
            return self.triage_queue
        except Exception as e:
            # External namespace failure — dead-letter on primary namespace
            target_ns = self._triage_sb_namespace or self.namespace
            logger.error(
                f"Failed to send to triage queue '{self.triage_queue}' on namespace "
                f"'{target_ns}': {e}. Routing to dead-letter on primary namespace.",
                exc_info=True,
            )
            dlq_message = {
                **message_data,
                "_deadLetterReason": "triage_queue_send_failure",
                "_deadLetterError": str(e),
                "_originalQueue": self.triage_queue,
                "_originalNamespace": target_ns,
            }
            self._send_to_queue("dead-letter", dlq_message)
            raise
    
    def peek_queue(self, queue_name: str, max_count: int = 10) -> List[dict]:
        """
        Peek at messages in a queue without consuming them.
        
        Args:
            queue_name: Name of the queue to peek
            max_count: Maximum number of messages to peek
            
        Returns:
            List of message dictionaries
        """
        with self._get_sync_client() as client:
            receiver = client.get_queue_receiver(queue_name=queue_name)
            
            with receiver:
                messages = receiver.peek_messages(max_message_count=max_count)
                
                result = []
                for msg in messages:
                    body = self._parse_message_body(str(msg))
                    result.append({
                        "sequence_number": msg.sequence_number,
                        "enqueued_time": msg.enqueued_time_utc.isoformat() if msg.enqueued_time_utc else None,
                        "body": body
                    })
                
                return result


# Tool function definitions for agent framework
def get_queue_tool_definitions() -> list:
    """
    Returns the tool definitions for the Azure AI Agent framework.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "receive_email_from_intake",
                "description": (
                    "Receives the next email message from the email-intake queue. "
                    "The message is removed from the queue after successful processing. "
                    "Returns the email data including sender, subject, body text, and "
                    "any attachment information."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "route_email",
                "description": (
                    "Routes a classified email to the appropriate queue based on "
                    "confidence score. High confidence (>=80%) goes to classification-pending, "
                    "medium (65-79%) and low (<65%) go to human-review."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "email_id": {
                            "type": "string",
                            "description": "Unique identifier of the email being routed"
                        },
                        "confidence_score": {
                            "type": "number",
                            "description": "Classification confidence score between 0.0 and 1.0"
                        },
                        "classification": {
                            "type": "string",
                            "description": "Category assigned to the email",
                            "enum": [
                                "Capital calls",
                                "Distributions",
                                "Capital account statements",
                                "Other PE lifecycle events",
                                "Others"
                            ]
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Explanation for why this classification was chosen"
                        }
                    },
                    "required": ["email_id", "confidence_score", "classification", "reasoning"]
                }
            }
        }
    ]
