"""
Email Classification Agent using Azure AI Agent Service.
Implements a 2-step classification approach:
1. Relevance check (subject + body) - Binary: PE-related or not
2. Full classification (subject + body + attachment content) - PE event type
"""

import os
import json
import logging
import asyncio
import re
from typing import Optional, Union
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
    SFTP_CLASSIFICATION_USER_PROMPT,
    RELEVANCE_OUTPUT_SCHEMA,
    CLASSIFICATION_OUTPUT_SCHEMA,
    PE_CATEGORIES,
    NON_PE_CATEGORY
)
from .tools.queue_tools import QueueTools
from .tools.graph_tools import GraphAPITools
from .tools.document_intelligence_tool import DocumentIntelligenceTool
from .tools.cosmos_tools import CosmosDBTools
from .tools.link_download_tool import LinkDownloadTool

logger = logging.getLogger(__name__)


def parse_bool(value: Union[str, bool, None]) -> bool:
    """
    Parse a value that might be a string boolean (from Logic App) to actual boolean.
    Handles: "True", "true", "TRUE", "False", "false", true, false, None, etc.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1")
    return bool(value)


def extract_plain_text_from_html(html: str) -> str:
    """
    Extract plain text from HTML email body.
    Removes HTML tags and extracts readable text.
    """
    if not html:
        return ""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', html)
    # Decode HTML entities
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


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
        self.link_download_tool = LinkDownloadTool(cosmos_tools=self.cosmos_tools)
        
        # Pipeline mode configuration
        self.pipeline_mode = os.getenv("PIPELINE_MODE", "full")
        
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

        # Detect intake source: "sftp" for SFTP-sourced files, default to "email"
        intake_source = email_data.get("intakeSource", "email")

        if intake_source == "sftp":
            # SFTP-sourced PDF: use dedupKey (= Cosmos document id) for lookup
            # Falls back to fileId for backward compatibility with older messages
            email_id = email_data.get("dedupKey") or email_data.get("fileId") or email_data.get("id") or "unknown"
            logger.info(f"Processing SFTP file: {email_id[:30]}...")
        else:
            # Email-sourced: use emailId or id
            email_id = email_data.get("emailId") or email_data.get("id") or "unknown"
            logger.info(f"Processing email: {email_id[:30]}...")
        
        # Log the start of processing
        self.cosmos_tools.log_classification_event(
            email_id=email_id,
            event_type="processing_started",
            details={"received_from_queue": datetime.utcnow().isoformat()}
        )
        
        try:
            # =====================================================
            # STEP 1: PE RELEVANCE CHECK (Binary: YES/NO)
            # =====================================================
            # - For SFTP PDFs: always relevant (curated source), skip LLM call
            # - For emails: binary decision based on metadata + attachment names
            if intake_source == "sftp":
                relevance_result = {
                    "is_relevant": True,
                    "confidence": 1.0,
                    "reasoning": "SFTP-sourced PDF — auto-relevant (curated intake channel)",
                    "initial_category": email_data.get("fileType", "Others")
                }
                logger.info("SFTP source → auto-relevant, skipping relevance check")
            else:
                relevance_result = await self._check_relevance(email_data)
            
            # OVERRIDE: If subject contains "PE" and (has attachments OR body mentions docs), force relevance
            # This handles cases where the model incorrectly marks PE emails as not relevant
            # Skip for SFTP-sourced records (no email subject/body available)
            if intake_source != "sftp":
                subject = email_data.get("subject", "").lower()
                body_text = email_data.get("bodyText", email_data.get("emailBody", "")).lower()
                
                # Extract plain text if HTML
                if "<html" in body_text or "<body" in body_text:
                    body_text = extract_plain_text_from_html(body_text).lower()
                
                # Check for PE indicators in subject
                subject_has_pe = any(term in subject for term in [
                    "pe ", "pe documents", "pe docs", "private equity", 
                    "capital call", "distribution", "appel de fonds"
                ]) or subject.startswith("pe")
                
                # Check if we have attachments (properly parse string boolean)
                has_attachments = parse_bool(email_data.get("hasAttachments", False))
                attachment_count = email_data.get("attachmentCount", 0)
                if isinstance(attachment_count, str):
                    try:
                        attachment_count = int(attachment_count)
                    except ValueError:
                        attachment_count = 0
                
                # Check if body mentions attachments
                body_mentions_attachments = any(word in body_text for word in [
                    "attached", "document", "docs", "fichier", "pièce jointe", "enclosed"
                ])
                
                # Override logic: if subject has PE and we have attachments or body mentions docs
                should_override = (
                    not relevance_result.get("is_relevant", False) 
                    and subject_has_pe 
                    and (has_attachments or attachment_count > 0 or body_mentions_attachments)
                )
                
                if should_override:
                    logger.warning(f"⚠️ OVERRIDE: Subject contains PE term, has_attachments={has_attachments}, "
                                 f"attachment_count={attachment_count}, body_mentions_docs={body_mentions_attachments}")
                    logger.warning(f"   Forcing relevance=true to allow full classification with attachments")
                    relevance_result["is_relevant"] = True
                    relevance_result["reasoning"] = f"OVERRIDE: {relevance_result.get('reasoning', '')} [Forced relevant: subject='{email_data.get('subject', '')}', hasAttachments={has_attachments}, attachmentCount={attachment_count}]"
                    relevance_result["initial_category"] = "Capital Call"  # Default guess
            
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
            
            # If NOT PE-relevant → route to discarded queue immediately
            if not relevance_result.get("is_relevant", False):
                logger.info(f"❌ Email NOT PE-relevant → DISCARDED: {relevance_result.get('reasoning', '')[:100]}")
                
                # Route to discarded queue
                target_queue = self.queue_tools.route_email(
                    email_data=email_data,
                    confidence_score=relevance_result.get("confidence", 0.0),
                    classification=NON_PE_CATEGORY,
                    classification_details=relevance_result
                )
                
                # Update email status to "discarded" in Cosmos DB
                self.cosmos_tools.update_email_classification(
                    email_id=email_id,
                    classification=NON_PE_CATEGORY,
                    confidence_score=relevance_result.get("confidence", 0.0),
                    classification_details=relevance_result,
                    step="final",  # Use "final" to trigger status update
                    email_data=email_data
                )
                
                return {
                    "email_id": email_id,
                    "step": "relevance_only",
                    "is_relevant": False,
                    "category": NON_PE_CATEGORY,
                    "confidence": relevance_result.get("confidence", 0.0),
                    "routed_to": target_queue
                }
            
            logger.info(f"✅ Email IS PE-relevant → proceeding to PE event classification")
            
            # =====================================================
            # STEP 1.5: DOWNLOAD LINKED DOCUMENTS (if any)
            # =====================================================
            # Skip for SFTP-sourced records — file already in blob storage
            cosmos_doc = None
            if intake_source != "sftp":
                # Detect document download links in email body, fetch them,
                # and add to attachmentPaths as {"path": ..., "source": "link"}
                try:
                    # Fetch full email body from Cosmos DB — the Service Bus message
                    # only contains bodyPreview (~255 chars) which may truncate URLs.
                    email_body_for_links = ""
                    cosmos_doc = self.cosmos_tools.get_email_document(email_id)
                    if cosmos_doc:
                        email_body_for_links = cosmos_doc.get("emailBody", "")
                    if not email_body_for_links:
                        # Fallback to Service Bus fields if Cosmos body unavailable
                        email_body_for_links = email_data.get("bodyText", "") or email_data.get("emailBody", "")
                    link_partition_key = cosmos_doc.get("partitionKey") if cosmos_doc else None
                    link_result = await self.link_download_tool.process_email_links(
                        email_id=email_id,
                        email_body=email_body_for_links,
                        partition_key=link_partition_key,
                    )

                    if link_result.downloaded_files:
                        logger.info(f"📎 Downloaded {len(link_result.downloaded_files)} linked document(s)")
                        # Merge into attachmentPaths
                        attachment_paths = email_data.get("attachmentPaths") or []
                        for downloaded in link_result.downloaded_files:
                            attachment_paths.append({
                                "path": downloaded.path,
                                "source": "link",
                                "url": downloaded.url,
                                "content_type": downloaded.content_type,
                            })
                        email_data["attachmentPaths"] = attachment_paths

                        # Update hasAttachments and attachmentsCount (plural — matches Cosmos schema)
                        email_data["hasAttachments"] = True
                        current_count = email_data.get("attachmentsCount", 0)
                        if isinstance(current_count, str):
                            try:
                                current_count = int(current_count)
                            except ValueError:
                                current_count = 0
                        email_data["attachmentsCount"] = current_count + len(link_result.downloaded_files)

                    if link_result.failures:
                        logger.warning(f"⚠️ {len(link_result.failures)} link download(s) failed")

                    # Stash results for Cosmos DB persistence (T010)
                    email_data["_link_download_result"] = {
                        "urls_detected": link_result.urls_detected,
                        "urls_attempted": link_result.urls_attempted,
                        "downloaded_count": len(link_result.downloaded_files),
                        "failure_count": len(link_result.failures),
                        "failures": [
                            {
                                "url": f.url,
                                "error": f.error,
                                "attempted_at": f.attempted_at,
                                "error_type": f.error_type,
                                "http_status": f.http_status,
                            }
                            for f in link_result.failures
                        ],
                    }

                    self.cosmos_tools.log_classification_event(
                        email_id=email_id,
                        event_type="link_download_complete",
                        details=email_data["_link_download_result"],
                    )
                except Exception as e:
                    logger.error(f"Error during link download pre-processing: {e}", exc_info=True)
                    # Don't block email processing — continue without downloaded links

                # ── Reconcile attachment info from Cosmos (authoritative source) ──
                # The Logic App writes attachment paths to Cosmos before sending to
                # the intake queue, so Cosmos may have richer data than the queue msg.
                if cosmos_doc:
                    cosmos_att_paths = cosmos_doc.get("attachmentPaths") or []
                    email_att_paths = email_data.get("attachmentPaths") or []
                    if len(cosmos_att_paths) > len(email_att_paths):
                        email_data["attachmentPaths"] = cosmos_att_paths
                    cosmos_att_count = cosmos_doc.get("attachmentsCount", 0)
                    email_att_count = email_data.get("attachmentsCount", 0)
                    if isinstance(email_att_count, str):
                        try:
                            email_att_count = int(email_att_count)
                        except ValueError:
                            email_att_count = 0
                    if isinstance(cosmos_att_count, str):
                        try:
                            cosmos_att_count = int(cosmos_att_count)
                        except ValueError:
                            cosmos_att_count = 0
                    if cosmos_att_count > email_att_count:
                        email_data["attachmentsCount"] = cosmos_att_count
                    if cosmos_doc.get("hasAttachments") and not email_data.get("hasAttachments"):
                        email_data["hasAttachments"] = True
            else:
                # SFTP source: file already in blob storage via Logic App
                # Ensure attachmentPaths includes the blob path for processing
                blob_path = email_data.get("blobPath")
                if blob_path and not email_data.get("attachmentPaths"):
                    email_data["attachmentPaths"] = [{"path": blob_path, "source": "sftp"}]
                    email_data["hasAttachments"] = True
                    email_data["attachmentsCount"] = 1

            # =====================================================
            # STEP 2: PROCESS ATTACHMENTS (for PE-relevant emails)
            # =====================================================
            attachment_analysis = await self._process_attachments(email_data)
            
            # Log attachment processing
            if attachment_analysis:
                self.cosmos_tools.log_classification_event(
                    email_id=email_id,
                    event_type="attachments_processed",
                    details={"attachment_count": len(attachment_analysis)}
                )
            
            # =====================================================
            # PIPELINE MODE BRANCH (after pre-processing, before classification)
            # =====================================================
            logger.info(f"Pipeline mode: {self.pipeline_mode} for email {email_id[:30]}")

            if self.pipeline_mode == "triage-only":
                # ── TRIAGE-ONLY: skip classification (Steps 3–5) ──
                relevance_block = {
                    "isRelevant": True,
                    "confidence": relevance_result.get("confidence", 0.0),
                    "initialCategory": relevance_result.get("initial_category", ""),
                    "reasoning": relevance_result.get("reasoning", ""),
                }
                triage_message = {
                    "emailId": email_id,
                    "from": email_data.get("from", email_data.get("sender", "unknown")),
                    "subject": email_data.get("subject", ""),
                    "receivedAt": email_data.get("receivedAt", email_data.get("received_at", "")),
                    "hasAttachments": parse_bool(email_data.get("hasAttachments", False)),
                    "attachmentsCount": email_data.get("attachmentsCount", 0),
                    "attachmentPaths": email_data.get("attachmentPaths", []),
                    "intakeSource": intake_source,
                    "relevance": relevance_block,
                    "pipelineMode": "triage-only",
                    "status": "triaged",
                    "processedAt": datetime.utcnow().isoformat(),
                    "routing": {
                        "sourceQueue": self.queue_tools.QUEUE_EMAIL_INTAKE,
                        "targetQueue": self.queue_tools.triage_queue,
                        "routedAt": datetime.utcnow().isoformat(),
                    },
                }
                # Add SFTP-specific fields to triage message
                if intake_source == "sftp":
                    triage_message["originalFilename"] = email_data.get("originalFilename", "")
                    triage_message["fileType"] = email_data.get("fileType", "")
                    triage_message["blobPath"] = email_data.get("blobPath", "")

                triage_target = self.queue_tools.send_to_triage_queue(triage_message)

                # Update Cosmos with triage-only pipeline details
                triage_classification_details = {
                    **relevance_result,
                    "pipelineMode": "triage-only",
                    "stepsExecuted": ["triage", "pre-processing", "routing"],
                    "targetQueue": triage_target,
                }
                self.cosmos_tools.update_email_classification(
                    email_id=email_id,
                    classification=relevance_result.get("initial_category", ""),
                    confidence_score=relevance_result.get("confidence", 0.0),
                    classification_details=triage_classification_details,
                    step="final",
                    email_data=email_data,
                )

                # ── PE EVENT UPSERT (triage-only) ──
                # Create/update a PE event record so the dashboard shows Unique Events
                initial_cat = relevance_result.get("initial_category", "")
                if initial_cat and initial_cat != NON_PE_CATEGORY:
                    try:
                        pe_event, is_dup = self.cosmos_tools.find_or_create_pe_event(
                            email_id=email_id,
                            classification_details={
                                "category": initial_cat,
                                "pe_company": relevance_result.get("pe_company", "Unknown"),
                                "fund_name": relevance_result.get("fund_name", "Unknown"),
                                "confidence": relevance_result.get("confidence", 0.0),
                                "reasoning": relevance_result.get("reasoning", ""),
                                "key_evidence": relevance_result.get("key_evidence", []),
                            },
                            intake_source=intake_source,
                            received_at=email_data.get("receivedAt", email_data.get("received_at", "")),
                        )
                        pe_event_id = pe_event.get("id") if pe_event else None
                        logger.info(f"PE event {'linked' if is_dup else 'created'}: {pe_event_id} (triage-only)")
                    except Exception as e:
                        logger.warning(f"Failed to upsert PE event in triage-only mode: {e}")

                self.cosmos_tools.log_classification_event(
                    email_id=email_id,
                    event_type="triage_complete",
                    details={"pipeline_mode": "triage-only", "routed_to": triage_target},
                )

                return {
                    "email_id": email_id,
                    "step": "triage_only",
                    "is_relevant": True,
                    "category": relevance_result.get("initial_category", ""),
                    "confidence": relevance_result.get("confidence", 0.0),
                    "routed_to": triage_target,
                    "pipeline_mode": "triage-only",
                }

            # ── FULL MODE: continue with classification (Steps 3–5) ──
            
            # =====================================================
            # STEP 3: PE EVENT TYPE CLASSIFICATION
            # =====================================================
            # Classify into one of the 10 PE event types.
            # The confidence score from this step determines routing:
            # - confidence >= 65% → archival-pending queue
            # - confidence < 65%  → human-review queue
            classification_result = await self._classify_email(email_data, attachment_analysis)
            
            # Log classification
            self.cosmos_tools.log_classification_event(
                email_id=email_id,
                event_type="classification_complete",
                details=classification_result
            )
            
            # Update email with final classification
            full_classification_details = {
                **classification_result,
                "pipelineMode": "full",
                "stepsExecuted": ["triage", "pre-processing", "classification", "routing"],
            }
            self.cosmos_tools.update_email_classification(
                email_id=email_id,
                classification=classification_result.get("category", "Capital Call"),  # Default to Capital Call for PE emails
                confidence_score=classification_result.get("confidence", 0.0),
                classification_details=full_classification_details,
                step="final",
                email_data=email_data
            )
            
            # =====================================================
            # STEP 4a: PE EVENT DEDUPLICATION
            # =====================================================
            is_duplicate = False
            pe_event_id = None
            if classification_result.get("category") not in ["Not PE Related"]:
                try:
                    pe_event, is_duplicate = self.cosmos_tools.find_or_create_pe_event(
                        email_id=email_id,
                        classification_details=classification_result,
                        intake_source=intake_source,
                        received_at=email_data.get("receivedAt", email_data.get("received_at", "")),
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
                        classification=classification_result.get("category", "Capital Call")
                    )
            
            # =====================================================
            # STEP 5: ROUTE BASED ON CLASSIFICATION CONFIDENCE
            # =====================================================
            # Routing logic (for PE-relevant emails only):
            # - confidence >= 65% → archival-pending queue (ready for archival)
            # - confidence < 65%  → human-review queue (needs human disambiguation)
            confidence = classification_result.get("confidence", 0.0)
            category = classification_result.get("category", "Capital Call")
            
            logger.info(f"📊 Classification: {category} (confidence: {confidence:.1%})")
            if confidence >= 0.65:
                logger.info(f"   → Routing to archival-pending (confidence >= 65%)")
            else:
                logger.info(f"   → Routing to human-review (confidence < 65%)")
            
            target_queue = self.queue_tools.route_email(
                email_data=email_data,
                confidence_score=confidence,
                classification=category,
                classification_details=classification_result
            )
            
            return {
                "email_id": email_id,
                "step": "full_classification",
                "is_relevant": True,
                "is_duplicate": is_duplicate,
                "pe_event_id": pe_event_id,
                "category": category,
                "confidence": confidence,
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
        Step 1: Binary relevance check - Is this email PE-related or not?
        
        This check uses email metadata INCLUDING attachment names to make a
        binary decision. Attachment names are the strongest indicator.
        
        Args:
            email_data: Email content from queue
            
        Returns:
            Relevance check result with is_relevant (bool) decision
        """
        logger.info("Performing PE relevance check (binary decision)...")
        
        # Parse hasAttachments properly (Logic App may send string "True"/"False")
        has_attachments = parse_bool(email_data.get("hasAttachments", False))
        attachment_count = email_data.get("attachmentCount", 0)
        if isinstance(attachment_count, str):
            try:
                attachment_count = int(attachment_count)
            except ValueError:
                attachment_count = 0
        
        logger.info(f"  hasAttachments: {has_attachments}, attachmentCount: {attachment_count}")
        
        # Get attachment paths - these are CRITICAL for relevance detection
        # Supports both legacy string[] and new object[] ({path, source}) format
        attachment_paths = email_data.get("attachmentPaths", []) or email_data.get("attachmentNames", [])
        
        # Extract just the filename from paths (Logic App may send full path like "messageId/filename.pdf")
        attachment_names = []
        for entry in attachment_paths:
            # Backward-compatible: handle both string and object entries
            path = entry.get("path") if isinstance(entry, dict) else entry
            if "/" in path:
                # Extract filename after the last /
                filename = path.split("/")[-1]
                attachment_names.append(filename)
            else:
                attachment_names.append(path)
        
        if not attachment_names and (has_attachments or attachment_count > 0):
            attachment_names = [f"[{attachment_count} attachment(s) present but names not available]"]
        
        logger.info(f"  Attachment names: {attachment_names}")
        
        # Format attachment names prominently for the model
        attachment_names_str = "\n".join([f"  - {name}" for name in attachment_names]) if attachment_names else "None"
        
        # Get body text - handle both plain text and HTML
        body_text = email_data.get("bodyText", "") or email_data.get("emailBody", "")
        if "<html" in body_text.lower() or "<body" in body_text.lower():
            body_text = extract_plain_text_from_html(body_text)
        body_text = body_text[:2000]  # Limit body text
        
        # Prepare the user prompt with attachment names prominently displayed
        user_prompt = RELEVANCE_CHECK_USER_PROMPT.format(
            sender=email_data.get("from", email_data.get("sender", "Unknown")),
            subject=email_data.get("subject", "No subject"),
            received_date=email_data.get("receivedAt", "Unknown"),
            body_text=body_text,
            has_attachments=has_attachments,
            attachment_names=attachment_names_str
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
                        raw_response = content.text.value
                        logger.info(f"Raw relevance response (first 500 chars): {raw_response[:500]}")
                        
                        # Try to extract JSON from the response
                        parsed = self._extract_json_from_response(raw_response)
                        if parsed:
                            return parsed
                        
                        logger.warning(f"Failed to parse relevance response as JSON. Full response: {raw_response}")
        
        # Default response if parsing fails - default to relevant for safety
        return {
            "is_relevant": True,  # Default to relevant for safety
            "confidence": 0.5,
            "reasoning": "Unable to parse agent response",
            "initial_category": "Others"
        }
    
    async def _process_attachments(self, email_data: dict) -> list:
        """
        Download and process PDF attachments using Document Intelligence.
        
        For email sources: downloads via Graph API.
        For SFTP sources: reads from Azure Blob Storage (already uploaded by Logic App).
        
        Args:
            email_data: Email/SFTP content with attachment info
            
        Returns:
            List of processed attachment results
        """
        # Properly parse hasAttachments (might be string "True" from Logic App)
        has_attachments = parse_bool(email_data.get("hasAttachments", False))
        attachment_count = email_data.get("attachmentCount", 0)
        if isinstance(attachment_count, str):
            try:
                attachment_count = int(attachment_count)
            except ValueError:
                attachment_count = 0
        
        # Also check attachmentPaths length
        attachment_paths = email_data.get("attachmentPaths", [])
        if attachment_paths:
            attachment_count = max(attachment_count, len(attachment_paths))
        
        if not has_attachments and attachment_count == 0:
            logger.info("No attachments to process")
            return []
        
        logger.info(f"Processing attachments (hasAttachments={has_attachments}, count={attachment_count})...")
        
        intake_source = email_data.get("intakeSource", "email")
        
        if intake_source == "sftp":
            return await self._process_sftp_attachments(email_data, attachment_paths)
        
        # ── Email source: download via Graph API ──
        graph_info = self.graph_tools.extract_email_info_from_message(email_data)
        user_id = graph_info.get("user_id")
        message_id = graph_info.get("message_id")
        
        logger.info(f"Graph API identifiers - user_id: {user_id}, message_id: {message_id[:50] if message_id else 'None'}...")
        
        if not user_id or not message_id:
            logger.warning("Missing user_id or message_id for attachment download")
            logger.warning(f"  email_data keys: {list(email_data.keys())}")
            return []
        
        # Download PDF attachments
        try:
            logger.info(f"Calling Graph API to download attachments...")
            pdf_attachments = await self.graph_tools.download_all_pdf_attachments(
                user_id=user_id,
                message_id=message_id
            )
            logger.info(f"Downloaded {len(pdf_attachments)} PDF attachment(s)")
            
            if len(pdf_attachments) == 0:
                logger.warning("Graph API returned 0 PDF attachments - check permissions or attachment types")
        except Exception as e:
            logger.error(f"Error downloading attachments: {e}", exc_info=True)
            return []
        
        # Process each attachment with Document Intelligence
        results = []
        for attachment in pdf_attachments:
            try:
                content_bytes = attachment.get("content_decoded")
                if content_bytes:
                    logger.info(f"Analyzing attachment: {attachment.get('name', 'unknown')}")
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
    
    async def _process_sftp_attachments(self, email_data: dict, attachment_paths: list) -> list:
        """
        Process SFTP-sourced PDF attachments from Azure Blob Storage.
        
        The Logic App has already uploaded the file to blob storage.
        This method downloads from blob and processes with Document Intelligence.
        """
        from azure.identity.aio import DefaultAzureCredential
        from azure.storage.blob.aio import BlobServiceClient

        storage_url = os.environ.get("STORAGE_ACCOUNT_URL")
        if not storage_url:
            logger.error("STORAGE_ACCOUNT_URL not set — cannot read SFTP blob")
            return []

        results = []
        credential = DefaultAzureCredential()
        blob_service = BlobServiceClient(account_url=storage_url, credential=credential)

        try:
            async with blob_service:
                container_client = blob_service.get_container_client("attachments")
                for entry in attachment_paths:
                    blob_path = entry.get("path") if isinstance(entry, dict) else entry
                    filename = blob_path.split("/")[-1] if "/" in blob_path else blob_path
                    try:
                        blob_client = container_client.get_blob_client(blob_path)
                        download = await blob_client.download_blob()
                        content_bytes = await download.readall()
                        logger.info(f"Downloaded SFTP blob: {blob_path} ({len(content_bytes)} bytes)")

                        extracted = await self.doc_intel_tool.analyze_document_from_bytes(
                            document_bytes=content_bytes,
                            filename=filename,
                        )
                        results.append({
                            "name": filename,
                            "size": len(content_bytes),
                            "extracted_content": extracted,
                        })
                    except Exception as e:
                        logger.error(f"Error processing SFTP blob {blob_path}: {e}", exc_info=True)
        finally:
            await credential.close()

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
        
        # Get attachment names from email_data as fallback
        # Supports both legacy string[] and new object[] ({path, source}) format
        attachment_paths = email_data.get("attachmentPaths", []) or email_data.get("attachmentNames", [])
        attachment_names = []
        for entry in attachment_paths:
            # Backward-compatible: handle both string and object entries
            path = entry.get("path") if isinstance(entry, dict) else entry
            if "/" in path:
                filename = path.split("/")[-1]
                attachment_names.append(filename)
            else:
                attachment_names.append(path)
        
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
        
        # If no attachment content was extracted, still provide attachment names as clues
        if not attachment_summary:
            if attachment_names:
                attachment_summary = "**Attachment files present (content extraction pending):**\n"
                for name in attachment_names:
                    attachment_summary += f"  - {name}\n"
                attachment_summary += "\n**IMPORTANT**: Use the attachment FILENAMES above as classification evidence. "
                attachment_summary += "For example, 'Appel de fonds' = Capital Call, 'Distribution' = Distribution Notice."
                logger.warning(f"No attachment content extracted, but filenames available: {attachment_names}")
            else:
                attachment_summary = "No PDF attachments found or processed."
        
        # Prepare the user prompt
        intake_source = email_data.get("intakeSource", "email")
        if intake_source == "sftp":
            user_prompt = SFTP_CLASSIFICATION_USER_PROMPT.format(
                original_filename=email_data.get("originalFilename", "unknown"),
                file_type=email_data.get("fileType", "unknown"),
                received_date=email_data.get("receivedAt", "Unknown"),
                account=email_data.get("account", "Unknown"),
                fund=email_data.get("fund", "Unknown"),
                doc_type=email_data.get("docType", "Unknown"),
                name=email_data.get("name", "Unknown"),
                published_date=email_data.get("publishedDate", "Unknown"),
                effective_date=email_data.get("effectiveDate", "Unknown"),
                attachment_analysis=attachment_summary,
            )
        else:
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
                        raw_response = content.text.value
                        logger.info(f"Raw classification response (first 500 chars): {raw_response[:500]}")
                        
                        # Try to extract JSON from the response
                        parsed = self._extract_json_from_response(raw_response)
                        if parsed:
                            return parsed
                        
                        logger.warning(f"Failed to parse classification response as JSON. Full response: {raw_response}")
        
        # Default response if parsing fails - but try to infer from attachment names
        # Supports both legacy string[] and new object[] ({path, source}) format
        attachment_paths = email_data.get("attachmentPaths", [])
        attachment_names = [
            (entry.get("path") if isinstance(entry, dict) else entry).split("/")[-1]
            if "/" in (entry.get("path") if isinstance(entry, dict) else entry)
            else (entry.get("path") if isinstance(entry, dict) else entry)
            for entry in attachment_paths
        ]
        
        # Improved keyword-based fallback classification with confidence boosting
        names_lower = " ".join(attachment_names).lower()
        inferred_category = "Capital Call"  # Default for PE emails
        inferred_confidence = 0.5
        
        # Count how many attachments match each category
        capital_call_matches = sum(1 for name in attachment_names if any(kw in name.lower() for kw in ["appel de fonds", "capital call", "drawdown"]))
        distribution_matches = sum(1 for name in attachment_names if "distribution" in name.lower())
        nav_matches = sum(1 for name in attachment_names if any(kw in name.lower() for kw in ["nav", "capital account", "relevé de compte"]))
        
        if capital_call_matches > 0:
            inferred_category = "Capital Call"
            # Multiple matches = higher confidence
            inferred_confidence = min(0.95, 0.80 + (capital_call_matches * 0.03))
        elif distribution_matches > 0:
            inferred_category = "Distribution Notice"
            inferred_confidence = min(0.95, 0.80 + (distribution_matches * 0.03))
        elif nav_matches > 0:
            inferred_category = "Capital Account Statement"
            inferred_confidence = min(0.95, 0.80 + (nav_matches * 0.03))
        
        logger.warning(f"Using fallback classification based on attachment names: {inferred_category} ({inferred_confidence:.2f})")
        
        return {
            "category": inferred_category,
            "confidence": inferred_confidence,
            "reasoning": f"Fallback classification based on {len(attachment_names)} attachment filenames containing clear PE terminology",
            "key_evidence": attachment_names[:5],
            "fund_name": "Unknown",
            "pe_company": "Unknown"
        }
    
    def _extract_json_from_response(self, response: str) -> Optional[dict]:
        """
        Extract JSON from a model response that might contain markdown or extra text.
        
        Args:
            response: Raw model response string
            
        Returns:
            Parsed JSON dict or None if parsing fails
        """
        # Try direct JSON parse first
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        
        # Try to extract JSON from markdown code blocks
        import re
        
        # Pattern 1: ```json ... ```
        json_block_match = re.search(r'```json\s*([\s\S]*?)\s*```', response, re.IGNORECASE)
        if json_block_match:
            try:
                return json.loads(json_block_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Pattern 2: ``` ... ``` (generic code block)
        code_block_match = re.search(r'```\s*([\s\S]*?)\s*```', response)
        if code_block_match:
            try:
                return json.loads(code_block_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Pattern 3: Find JSON object pattern { ... }
        json_obj_match = re.search(r'\{[\s\S]*\}', response)
        if json_obj_match:
            try:
                return json.loads(json_obj_match.group(0))
            except json.JSONDecodeError:
                pass
        
        return None
    
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
