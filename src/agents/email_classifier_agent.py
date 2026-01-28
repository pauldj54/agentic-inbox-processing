"""
Email Classification Agent using Azure AI Agent Service.
Implements a 2-step classification approach:
1. Relevance check (subject + body)
2. Full classification (subject + body + attachment content)
"""

import os
import json
import logging
import asyncio
from typing import Optional
from datetime import datetime
from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import (
    Agent,
    AgentThread,
    MessageRole,
    RunStatus,
    ThreadMessage,
)

from .classification_prompts import (
    RELEVANCE_CHECK_SYSTEM_PROMPT,
    FULL_CLASSIFICATION_SYSTEM_PROMPT,
    RELEVANCE_CHECK_USER_PROMPT,
    FULL_CLASSIFICATION_USER_PROMPT,
    RELEVANCE_OUTPUT_SCHEMA,
    CLASSIFICATION_OUTPUT_SCHEMA,
    PE_CATEGORIES,
    NON_PE_CATEGORY
)
from .tools.queue_tools import QueueTools
from .tools.graph_tools import GraphAPITools
from .tools.document_intelligence_tool import DocumentIntelligenceTool
from .tools.cosmos_tools import CosmosDBTools

logger = logging.getLogger(__name__)


class EmailClassificationAgent:
    """
    Agent that processes emails from the intake queue, classifies them,
    and routes to appropriate queues based on confidence.
    """
    
    def __init__(
        self,
        endpoint: Optional[str] = None,
        model_deployment: str = "gpt-4o"
    ):
        """
        Initialize the Email Classification Agent.
        
        Args:
            endpoint: Azure AI Project endpoint URL
            model_deployment: Name of the deployed model to use
        """
        self.endpoint = endpoint or os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
        self.model_deployment = model_deployment or os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")
        
        if not self.endpoint:
            raise ValueError(
                "Azure AI Project endpoint is required. "
                "Set AZURE_AI_PROJECT_ENDPOINT environment variable."
            )
        
        # Initialize Agents client with endpoint
        self.credential = DefaultAzureCredential()
        self.agents_client = AgentsClient(
            endpoint=self.endpoint,
            credential=self.credential
        )
        
        # Initialize tools
        self.queue_tools = QueueTools()
        self.graph_tools = GraphAPITools()
        self.doc_intel_tool = DocumentIntelligenceTool()
        self.cosmos_tools = CosmosDBTools()
        
        # Agent instances (created on demand)
        self._relevance_agent = None
        self._classification_agent = None
    
    def _create_relevance_agent(self) -> Agent:
        """Create the relevance check agent."""
        return self.agents_client.create_agent(
            model=self.model_deployment,
            name="PE-Relevance-Checker",
            instructions=RELEVANCE_CHECK_SYSTEM_PROMPT,
            response_format={"type": "json_object"}
        )
    
    def _create_classification_agent(self) -> Agent:
        """Create the full classification agent."""
        return self.agents_client.create_agent(
            model=self.model_deployment,
            name="PE-Email-Classifier",
            instructions=FULL_CLASSIFICATION_SYSTEM_PROMPT,
            response_format={"type": "json_object"}
        )
    
    async def process_next_email(self) -> Optional[dict]:
        """
        Process the next email from the intake queue.
        
        Returns:
            Processing result dictionary or None if no emails available
        """
        logger.info("Checking for emails in intake queue...")
        
        # Step 0: Get email from queue
        email_message = self.queue_tools.receive_email_from_intake(max_wait_seconds=10)
        
        if not email_message:
            logger.info("No emails to process")
            return None
        
        email_data = email_message.get("body", {})
        # Try multiple field names for email ID (Logic App may use 'id' or 'emailId')
        email_id = email_data.get("emailId") or email_data.get("id") or "unknown"
        
        logger.info(f"Processing email: {email_id[:30]}...")
        
        # Log the start of processing
        self.cosmos_tools.log_classification_event(
            email_id=email_id,
            event_type="processing_started",
            details={"received_from_queue": datetime.utcnow().isoformat()}
        )
        
        try:
            # Step 1: Relevance Check
            relevance_result = await self._check_relevance(email_data)
            
            # Log relevance check
            self.cosmos_tools.log_classification_event(
                email_id=email_id,
                event_type="relevance_check",
                details=relevance_result
            )
            
            # Update email with relevance info
            self.cosmos_tools.update_email_classification(
                email_id=email_id,
                classification=relevance_result.get("initial_category", NON_PE_CATEGORY),
                confidence_score=relevance_result.get("confidence", 0.0),
                classification_details=relevance_result,
                step="relevance",
                email_data=email_data
            )
            
            # If not relevant, route to discarded queue and skip detailed classification
            if not relevance_result.get("is_relevant", False):
                logger.info(f"Email marked as not PE-relevant: {relevance_result.get('reasoning', '')[:100]}")
                
                # Route to discarded queue (available for manual review if needed)
                target_queue = self.queue_tools.route_email(
                    email_data=email_data,
                    confidence_score=relevance_result.get("confidence", 0.0),
                    classification=NON_PE_CATEGORY,
                    classification_details=relevance_result
                )
                
                return {
                    "email_id": email_id,
                    "step": "relevance_only",
                    "is_relevant": False,
                    "category": NON_PE_CATEGORY,
                    "confidence": relevance_result.get("confidence", 0.0),
                    "routed_to": target_queue
                }
            
            # Step 2: Process attachments if relevant
            attachment_analysis = await self._process_attachments(email_data)
            
            # Log attachment processing
            if attachment_analysis:
                self.cosmos_tools.log_classification_event(
                    email_id=email_id,
                    event_type="attachments_processed",
                    details={"attachment_count": len(attachment_analysis)}
                )
            
            # Step 3: Full Classification
            classification_result = await self._classify_email(email_data, attachment_analysis)
            
            # Log classification
            self.cosmos_tools.log_classification_event(
                email_id=email_id,
                event_type="classification_complete",
                details=classification_result
            )
            
            # Update email with final classification
            self.cosmos_tools.update_email_classification(
                email_id=email_id,
                classification=classification_result.get("category", "Others"),
                confidence_score=classification_result.get("confidence", 0.0),
                classification_details=classification_result,
                step="final",
                email_data=email_data
            )
            
            # Step 4a: Check for duplicate PE event and link
            is_duplicate = False
            pe_event_id = None
            if classification_result.get("category") not in ["Not PE Related", "Others"]:
                try:
                    pe_event, is_duplicate = self.cosmos_tools.find_or_create_pe_event(
                        email_id=email_id,
                        classification_details=classification_result
                    )
                    pe_event_id = pe_event.get("id") if pe_event else None
                    
                    if is_duplicate and pe_event_id:
                        # Mark email as duplicate
                        self.cosmos_tools.mark_email_as_duplicate(
                            email_id=email_id,
                            pe_event_id=pe_event_id
                        )
                        logger.info(f"Email is duplicate of PE event: {pe_event_id}")
                    else:
                        logger.info(f"New PE event created: {pe_event_id}")
                except Exception as e:
                    logger.warning(f"Failed to process PE event dedup: {e}")
            
            # Step 4b: Store extracted content in Cosmos
            for att in attachment_analysis:
                self.cosmos_tools.store_extracted_content(
                    email_id=email_id,
                    attachment_name=att.get("name", "unknown"),
                    extracted_content=att.get("extracted_content", {})
                )
                
                # Store tables separately for querying
                tables = att.get("extracted_content", {}).get("tables", [])
                for idx, table in enumerate(tables):
                    self.cosmos_tools.store_table_data(
                        email_id=email_id,
                        attachment_name=att.get("name", "unknown"),
                        table_index=idx,
                        table_data=table,
                        classification=classification_result.get("category", "Others")
                    )
            
            # Step 5: Route based on confidence
            target_queue = self.queue_tools.route_email(
                email_data=email_data,
                confidence_score=classification_result.get("confidence", 0.0),
                classification=classification_result.get("category", "Others"),
                classification_details=classification_result
            )
            
            return {
                "email_id": email_id,
                "step": "full_classification",
                "is_relevant": True,
                "is_duplicate": is_duplicate,
                "pe_event_id": pe_event_id,
                "classification": classification_result.get("category", "Others"),
                "confidence": classification_result.get("confidence", 0.0),
                "fund_name": classification_result.get("fund_name", "Unknown"),
                "pe_company": classification_result.get("pe_company", "Unknown"),
                "reasoning": classification_result.get("reasoning", ""),
                "attachments_processed": len(attachment_analysis),
                "routed_to": target_queue
            }
            
        except Exception as e:
            logger.error(f"Error processing email {email_id}: {e}")
            
            # Log error
            self.cosmos_tools.log_classification_event(
                email_id=email_id,
                event_type="processing_error",
                details={"error": str(e)}
            )
            
            raise
    
    async def _check_relevance(self, email_data: dict) -> dict:
        """
        Step 1: Quick relevance check based on email metadata.
        
        Args:
            email_data: Email content from queue
            
        Returns:
            Relevance check result
        """
        logger.info("Performing relevance check...")
        
        # Prepare the user prompt
        user_prompt = RELEVANCE_CHECK_USER_PROMPT.format(
            sender=email_data.get("from", "Unknown"),
            subject=email_data.get("subject", "No subject"),
            received_date=email_data.get("receivedAt", "Unknown"),
            body_text=email_data.get("bodyText", "")[:2000],  # Limit body text
            has_attachments=email_data.get("hasAttachments", False),
            attachment_names=", ".join(email_data.get("attachmentPaths", []))
        )
        
        # Create agent if not exists
        if not self._relevance_agent:
            self._relevance_agent = self._create_relevance_agent()
        
        # Create thread, add message, and run in one call
        run = self.agents_client.create_thread_and_process_run(
            agent_id=self._relevance_agent.id,
            thread={
                "messages": [
                    {"role": "user", "content": user_prompt}
                ]
            }
        )
        
        # Get the response messages from the run's thread
        messages = self.agents_client.messages.list(thread_id=run.thread_id)
        
        # Parse JSON response - messages is an iterable
        for msg in messages:
            if msg.role == "assistant":
                for content in msg.content:
                    if hasattr(content, 'text'):
                        try:
                            return json.loads(content.text.value)
                        except json.JSONDecodeError:
                            logger.warning("Failed to parse relevance response as JSON")
        
        # Default response if parsing fails
        return {
            "is_relevant": True,  # Default to relevant for safety
            "confidence": 0.5,
            "reasoning": "Unable to parse agent response",
            "initial_category": "Others"
        }
    
    async def _process_attachments(self, email_data: dict) -> list:
        """
        Download and process PDF attachments using Document Intelligence.
        
        Args:
            email_data: Email content with attachment info
            
        Returns:
            List of processed attachment results
        """
        if not email_data.get("hasAttachments", False):
            return []
        
        logger.info("Processing attachments...")
        
        # Get email identifiers for Graph API
        graph_info = self.graph_tools.extract_email_info_from_message(email_data)
        user_id = graph_info.get("user_id")
        message_id = graph_info.get("message_id")
        
        if not user_id or not message_id:
            logger.warning("Missing user_id or message_id for attachment download")
            return []
        
        # Download PDF attachments
        try:
            pdf_attachments = await self.graph_tools.download_all_pdf_attachments(
                user_id=user_id,
                message_id=message_id
            )
        except Exception as e:
            logger.error(f"Error downloading attachments: {e}")
            return []
        
        # Process each attachment with Document Intelligence
        results = []
        for attachment in pdf_attachments:
            try:
                content_bytes = attachment.get("content_decoded")
                if content_bytes:
                    extracted = await self.doc_intel_tool.analyze_document_from_bytes(
                        document_bytes=content_bytes,
                        filename=attachment.get("name", "document.pdf")
                    )
                    
                    results.append({
                        "name": attachment.get("name"),
                        "size": attachment.get("size"),
                        "extracted_content": extracted
                    })
                    
            except Exception as e:
                logger.error(f"Error processing attachment {attachment.get('name')}: {e}")
        
        return results
    
    async def _classify_email(self, email_data: dict, attachment_analysis: list) -> dict:
        """
        Step 2: Full classification with email and attachment content.
        
        Args:
            email_data: Email content
            attachment_analysis: List of processed attachment results
            
        Returns:
            Classification result
        """
        logger.info("Performing full classification...")
        
        # Build attachment analysis summary
        attachment_summary = ""
        for att in attachment_analysis:
            extracted = att.get("extracted_content", {})
            attachment_summary += f"\n### Attachment: {att.get('name')}\n"
            attachment_summary += f"Pages: {extracted.get('page_count', 0)}\n"
            attachment_summary += f"Tables found: {extracted.get('table_count', 0)}\n"
            attachment_summary += f"\n**Text content (first 1500 chars):**\n"
            attachment_summary += extracted.get("full_text", "")[:1500]
            
            # Add table summaries if present
            tables = extracted.get("tables", [])
            if tables:
                attachment_summary += f"\n\n**Tables ({len(tables)} found):**\n"
                for i, table in enumerate(tables[:3]):  # Limit to first 3 tables
                    attachment_summary += f"\nTable {i+1} ({table.get('row_count')}x{table.get('column_count')}):\n"
                    rows = table.get("rows", [])[:5]  # First 5 rows
                    for row in rows:
                        attachment_summary += " | ".join(str(cell)[:30] for cell in row) + "\n"
            
            attachment_summary += "\n---\n"
        
        if not attachment_summary:
            attachment_summary = "No PDF attachments found or processed."
        
        # Prepare the user prompt
        user_prompt = FULL_CLASSIFICATION_USER_PROMPT.format(
            sender=email_data.get("from", "Unknown"),
            subject=email_data.get("subject", "No subject"),
            received_date=email_data.get("receivedAt", "Unknown"),
            body_text=email_data.get("bodyText", "")[:3000],
            attachment_analysis=attachment_summary
        )
        
        # Create agent if not exists
        if not self._classification_agent:
            self._classification_agent = self._create_classification_agent()
        
        # Create thread and run in one call (new SDK pattern)
        run = self.agents_client.create_thread_and_process_run(
            agent_id=self._classification_agent.id,
            thread={"messages": [{"role": "user", "content": user_prompt}]}
        )
        
        # Get the response messages
        messages = self.agents_client.messages.list(thread_id=run.thread_id)
        
        # Parse JSON response - messages is an iterable
        for msg in messages:
            if msg.role == "assistant":
                for content in msg.content:
                    if hasattr(content, 'text'):
                        try:
                            return json.loads(content.text.value)
                        except json.JSONDecodeError:
                            logger.warning("Failed to parse classification response as JSON")
        
        # Default response if parsing fails
        return {
            "category": "Others",
            "confidence": 0.3,
            "reasoning": "Unable to parse agent response",
            "key_evidence": []
        }
    
    def cleanup(self):
        """Clean up agent resources."""
        if self._relevance_agent:
            try:
                self.agents_client.delete_agent(self._relevance_agent.id)
            except:
                pass
        
        if self._classification_agent:
            try:
                self.agents_client.delete_agent(self._classification_agent.id)
            except:
                pass


async def run_agent_loop(max_iterations: int = 10, wait_seconds: int = 30):
    """
    Run the agent in a loop, processing emails as they arrive.
    
    Args:
        max_iterations: Maximum number of emails to process (0 for infinite)
        wait_seconds: Seconds to wait between checks when queue is empty
    """
    agent = EmailClassificationAgent()
    
    try:
        iteration = 0
        while max_iterations == 0 or iteration < max_iterations:
            try:
                result = await agent.process_next_email()
                
                if result:
                    logger.info(f"Processed email: {json.dumps(result, indent=2)}")
                    iteration += 1
                else:
                    logger.info(f"No emails, waiting {wait_seconds}s...")
                    await asyncio.sleep(wait_seconds)
                    
            except Exception as e:
                logger.error(f"Error in processing loop: {e}")
                await asyncio.sleep(5)  # Brief pause before retry
                
    finally:
        agent.cleanup()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_agent_loop())
