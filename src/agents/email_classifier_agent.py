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
    NON_PE_CATEGORY,
    DOCUMENT_EVENTS_SYSTEM_PROMPT,
    DOCUMENT_EVENTS_USER_PROMPT,
)
from .tools.queue_tools import QueueTools
from .tools.graph_tools import GraphAPITools
from .tools.document_intelligence_tool import DocumentIntelligenceTool
from .tools.cosmos_tools import CosmosDBTools
from .tools.link_download_tool import LinkDownloadTool

logger = logging.getLogger(__name__)


EVENT_CATEGORY_KEYWORDS = [
    ("Capital Call", ["capital call", "appel de fonds", "drawdown"]),
    ("Distribution Notice", ["distribution notice", "redistribution notice", "distribution"]),
    ("Tax Statement", ["tax statement", "tax", "k-1", "k1"]),
    ("Subscription Agreement", ["subscription agreement", "subscription"]),
    ("Capital Account Statement", ["capital account", "nav", "statement of account"]),
    ("Quarterly Report", ["quarterly report", "quarterly"]),
    ("Annual Financial Statement", ["annual financial", "annual report", "financial statement"]),
    ("Legal Notice", ["legal notice", "notice to investors"]),
    ("Extension Notice", ["extension notice", "extension"]),
    ("Dissolution Notice", ["dissolution notice", "dissolution", "liquidation"]),
]


MISSING_FIELD_VALUES = {"", "unknown", "none", "null", "n/a", "not found"}


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
        self.relevance_timeout_seconds = int(os.getenv("RELEVANCE_CHECK_TIMEOUT_SECONDS", "90"))
        self.classification_timeout_seconds = int(os.getenv("CLASSIFICATION_TIMEOUT_SECONDS", "180"))
        self.document_events_timeout_seconds = int(os.getenv("DOCUMENT_EVENTS_TIMEOUT_SECONDS", "180"))
        
        # Agent instances (created on demand)
        self._relevance_agent = None
        self._classification_agent = None
        self._doc_events_agent = None
    
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

    def _create_doc_events_agent(self) -> Agent:
        """Create the per-document entity extraction agent (triage-only mode)."""
        return self.agents_client.create_agent(
            model=self.model_deployment,
            name="PE-Document-Entity-Extractor",
            instructions=DOCUMENT_EVENTS_SYSTEM_PROMPT,
            response_format={"type": "json_object"}
        )

    async def _run_agent_call_with_timeout(
        self,
        call_name: str,
        email_id: str,
        timeout_seconds: int,
        coroutine_func,
        *args,
        **kwargs,
    ):
        """Run an async agent SDK wrapper in a worker thread with a timeout."""
        def run_coroutine():
            return asyncio.run(coroutine_func(*args, **kwargs))

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(run_coroutine),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            message = f"{call_name} timed out after {timeout_seconds}s for {email_id[:30]}"
            logger.error(message)
            raise TimeoutError(message) from exc

    def _deterministic_relevance_check(self, email_data: dict) -> Optional[dict]:
        """Return a relevance result when filenames/subject clearly identify a PE document."""
        subject = str(email_data.get("subject", ""))
        attachment_paths = email_data.get("attachmentPaths", []) or email_data.get("attachmentNames", [])
        names = []
        for entry in attachment_paths:
            path = entry.get("originalName") or entry.get("path") if isinstance(entry, dict) else entry
            if path:
                names.append(str(path).split("/")[-1])

        evidence_text = " ".join([subject, *names]).lower().replace("_", " ").replace("-", " ")
        for category, keywords in EVENT_CATEGORY_KEYWORDS:
            if any(keyword in evidence_text for keyword in keywords):
                return {
                    "is_relevant": True,
                    "confidence": 0.98,
                    "reasoning": f"Deterministic relevance from subject/attachment name evidence: {', '.join(names) or subject}",
                    "initial_category": category,
                    "deterministic": True,
                }

        has_pe_indicator = " pe " in f" {evidence_text} " or "private equity" in evidence_text
        has_document = parse_bool(email_data.get("hasAttachments", False)) or bool(attachment_paths)
        if has_pe_indicator and has_document:
            return {
                "is_relevant": True,
                "confidence": 0.9,
                "reasoning": "Deterministic relevance from PE indicator plus stored document attachment.",
                "initial_category": "Others",
                "deterministic": True,
            }

        return None
    
    async def process_next_email(self) -> Optional[dict]:
        """
        Process the next email from the intake queue.
        
        Returns:
            Processing result dictionary or None if no emails available
        """
        return await self.queue_tools.process_email_from_intake(
            self._process_received_email,
            max_wait_seconds=10,
        )

    async def _process_received_email(self, email_message: dict) -> dict:
        """Process one already-received Service Bus message body."""
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
                relevance_result = self._deterministic_relevance_check(email_data)
                if relevance_result:
                    logger.info(
                        f"Deterministic relevance check matched: "
                        f"{relevance_result.get('initial_category')}"
                    )
                else:
                    relevance_result = await self._run_agent_call_with_timeout(
                        "relevance check",
                        email_id,
                        self.relevance_timeout_seconds,
                        self._check_relevance,
                        email_data,
                    )
            
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
            attachment_paths = email_data.get("attachmentPaths") or []
            expected_attachment_count = len(attachment_paths)
            if expected_attachment_count == 0:
                raw_count = email_data.get("attachmentsCount", email_data.get("attachmentCount", 0))
                try:
                    expected_attachment_count = int(raw_count or 0)
                except (TypeError, ValueError):
                    expected_attachment_count = 0
            attachment_processing_errors = email_data.get("_attachment_processing_errors", [])
            attachment_processing_failed = expected_attachment_count > 0 and not attachment_analysis
            
            # Log attachment processing
            if attachment_analysis:
                self.cosmos_tools.log_classification_event(
                    email_id=email_id,
                    event_type="attachments_processed",
                    details={"attachment_count": len(attachment_analysis)}
                )
            elif attachment_processing_failed:
                self.cosmos_tools.log_classification_event(
                    email_id=email_id,
                    event_type="attachments_processing_failed",
                    details={
                        "expected_attachment_count": expected_attachment_count,
                        "attachment_paths": [
                            entry.get("path") if isinstance(entry, dict) else entry
                            for entry in attachment_paths
                        ],
                        "error_count": len(attachment_processing_errors),
                        "errors": attachment_processing_errors,
                    }
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

                # Only send to triage-complete if there are actual stored documents
                # (email attachments, successful link downloads, or SFTP files).
                # If nothing was stored (e.g. all links blocked), there is nothing
                # actionable for downstream consumers.
                stored_docs = triage_message.get("attachmentPaths") or []
                has_stored_documents = len(stored_docs) > 0

                if has_stored_documents:
                    triage_target = self.queue_tools.send_to_triage_queue(triage_message)
                else:
                    triage_target = None
                    logger.info(
                        f"Skipping triage-complete queue — no stored documents for {email_id[:30]}"
                    )

                # Update Cosmos with triage-only pipeline details
                triage_classification_details = {
                    **relevance_result,
                    "pipelineMode": "triage-only",
                    "stepsExecuted": ["triage", "pre-processing", "routing"],
                    "targetQueue": triage_target if has_stored_documents else "none (no documents)",
                }
                self.cosmos_tools.update_email_classification(
                    email_id=email_id,
                    classification=relevance_result.get("initial_category", ""),
                    confidence_score=relevance_result.get("confidence", 0.0),
                    classification_details=triage_classification_details,
                    step="final",
                    email_data=email_data,
                )

                if attachment_processing_failed:
                    self.cosmos_tools.mark_processing_warning(
                        email_id=email_id,
                        warning_type="attachment_processing_failed",
                        message="Attachments were stored but could not be downloaded or analyzed; PE event extraction was skipped.",
                        details={
                            "expected_attachment_count": expected_attachment_count,
                            "error_count": len(attachment_processing_errors),
                            "errors": attachment_processing_errors,
                        },
                    )

                # ── STORE EXTRACTED CONTENT (triage-only) ──
                # Persist DI extraction (or failure markers) to Cosmos so
                # silent failures cannot hide. Without this we have zero
                # production visibility into what DI returned for each blob.
                for att in attachment_analysis:
                    try:
                        self.cosmos_tools.store_extracted_content(
                            email_id=email_id,
                            attachment_name=att.get("name", "unknown"),
                            extracted_content=att.get("extracted_content", {}),
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to persist extracted content for {att.get('name')}: {e}",
                            exc_info=True,
                        )

                # ── SURFACE DI / EXTRACTION FAILURES TO EMAIL RECORD ──
                di_failures = [
                    err for err in (email_data.get("_attachment_processing_errors") or [])
                    if err.get("stage") == "di_extract"
                ]
                if di_failures:
                    self.cosmos_tools.mark_processing_warning(
                        email_id=email_id,
                        warning_type="document_intelligence_failed",
                        message=(
                            f"{len(di_failures)} attachment(s) failed Document Intelligence "
                            f"extraction; downstream PE events will be flagged needs_attention."
                        ),
                        details={"failures": di_failures},
                    )

                # ── PE EVENT UPSERT (triage-only, per-document) ──
                # Extract per-document event attributes using the LLM,
                # then create/update a PE event record for each document.
                initial_cat = relevance_result.get("initial_category", "")
                if initial_cat and initial_cat != NON_PE_CATEGORY:
                    try:
                        per_doc_events = await self._run_agent_call_with_timeout(
                            "document event extraction",
                            email_id,
                            self.document_events_timeout_seconds,
                            self._extract_document_events,
                            attachment_analysis,
                            initial_category=initial_cat,
                        )
                        for doc_event in per_doc_events:
                            try:
                                pe_event, is_dup = self.cosmos_tools.find_or_create_pe_event(
                                    email_id=email_id,
                                    classification_details=doc_event,
                                    intake_source=intake_source,
                                    received_at=email_data.get("receivedAt", email_data.get("received_at", "")),
                                )
                                pe_event_id = pe_event.get("id") if pe_event else None
                                logger.info(
                                    f"PE event {'linked' if is_dup else 'created'} for "
                                    f"'{doc_event.get('document_name', '?')}': {pe_event_id} (triage-only)"
                                )
                            except Exception as e:
                                logger.error(
                                    f"Failed to upsert PE event for '{doc_event.get('document_name', '?')}': {e}",
                                    exc_info=True,
                                )
                                self.cosmos_tools.mark_processing_warning(
                                    email_id=email_id,
                                    warning_type="pe_event_upsert_failed",
                                    message=f"Could not upsert PE event for '{doc_event.get('document_name', '?')}'",
                                    details={"error": str(e), "error_type": type(e).__name__},
                                )
                    except Exception as e:
                        logger.error(
                            f"Failed per-document extraction in triage-only mode: {e}",
                            exc_info=True,
                        )
                        self.cosmos_tools.mark_processing_warning(
                            email_id=email_id,
                            warning_type="document_event_extraction_failed",
                            message="Per-document PE event extraction failed in triage-only mode",
                            details={"error": str(e), "error_type": type(e).__name__},
                        )

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
            classification_result = await self._run_agent_call_with_timeout(
                "classification",
                email_id,
                self.classification_timeout_seconds,
                self._classify_email,
                email_data,
                attachment_analysis,
            )
            
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

            if attachment_processing_failed:
                self.cosmos_tools.mark_processing_warning(
                    email_id=email_id,
                    warning_type="attachment_processing_failed",
                    message="Attachments were stored but could not be downloaded or analyzed; extracted content and PE event detail may be incomplete.",
                    details={
                        "expected_attachment_count": expected_attachment_count,
                        "error_count": len(attachment_processing_errors),
                        "errors": attachment_processing_errors,
                    },
                )
            
            # =====================================================
            # STEP 4a: PE EVENT DEDUPLICATION (per-document)
            # =====================================================
            is_duplicate = False
            pe_event_id = None
            pe_event_ids = []
            if classification_result.get("category") not in ["Not PE Related"]:
                # Use per-document pe_events array when available
                per_doc_events = classification_result.get("pe_events") or []
                if not per_doc_events:
                    # Fallback: treat the whole classification as a single event
                    per_doc_events = [classification_result]

                for doc_event in per_doc_events:
                    try:
                        pe_event, is_dup = self.cosmos_tools.find_or_create_pe_event(
                            email_id=email_id,
                            classification_details=doc_event,
                            intake_source=intake_source,
                            received_at=email_data.get("receivedAt", email_data.get("received_at", "")),
                        )
                        eid = pe_event.get("id") if pe_event else None
                        if eid:
                            pe_event_ids.append(eid)
                        if is_dup:
                            is_duplicate = True
                            logger.info(f"Document '{doc_event.get('document_name', '?')}' linked to existing PE event: {eid}")
                        else:
                            logger.info(f"New PE event created for '{doc_event.get('document_name', '?')}': {eid}")
                    except Exception as e:
                        logger.error(
                            f"Failed to process PE event dedup for '{doc_event.get('document_name', '?')}': {e}",
                            exc_info=True,
                        )
                        self.cosmos_tools.mark_processing_warning(
                            email_id=email_id,
                            warning_type="pe_event_dedup_failed",
                            message=f"Could not dedup PE event for '{doc_event.get('document_name', '?')}'",
                            details={"error": str(e), "error_type": type(e).__name__},
                        )

                pe_event_id = pe_event_ids[0] if pe_event_ids else None

                if is_duplicate and pe_event_id:
                    self.cosmos_tools.mark_email_as_duplicate(
                        email_id=email_id,
                        pe_event_id=pe_event_id
                    )
                    logger.info(f"Email is duplicate of PE event: {pe_event_id}")
            
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
        
        All sources (email, SFTP) read from Azure Blob Storage where the
        Logic App has already uploaded the files.
        
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
        
        email_data["_attachment_processing_errors"] = []

        if not has_attachments and attachment_count == 0:
            logger.info("No attachments to process")
            return []
        
        if not attachment_paths:
            logger.warning("hasAttachments is true but no attachmentPaths — nothing to download")
            email_data["_attachment_processing_errors"].append({
                "path": None,
                "error": "hasAttachments is true but no attachmentPaths were provided",
                "error_type": "MissingAttachmentPaths",
            })
            return []
        
        logger.info(f"Processing {len(attachment_paths)} attachment(s) from blob storage...")
        
        return await self._download_and_analyze_blobs(attachment_paths, email_data)

    async def _download_and_analyze_blobs(self, attachment_paths: list, email_data: dict | None = None) -> list:
        """
        Download attachments from blob storage and analyze with Document Intelligence.
        
        Used for both email and SFTP sources — the Logic App uploads all
        attachments to the 'attachments' container before queuing.
        """
        from azure.identity.aio import DefaultAzureCredential
        from azure.storage.blob.aio import BlobServiceClient

        storage_url = os.environ.get("STORAGE_ACCOUNT_URL")
        if not storage_url:
            logger.error("STORAGE_ACCOUNT_URL not set — cannot read blobs")
            if email_data is not None:
                email_data.setdefault("_attachment_processing_errors", []).append({
                    "path": None,
                    "error": "STORAGE_ACCOUNT_URL not set — cannot read blobs",
                    "error_type": "MissingStorageAccountUrl",
                })
            return []

        results = []
        credential = DefaultAzureCredential()
        blob_service = BlobServiceClient(account_url=storage_url, credential=credential)

        try:
            async with blob_service:
                container_client = blob_service.get_container_client("attachments")
                for entry in attachment_paths:
                    blob_path = entry.get("path") if isinstance(entry, dict) else entry
                    # Normalize: producers (notably the SFTP Logic App) sometimes
                    # emit paths like "/attachments/{id}/{name}" — strip leading
                    # slash and an optional "attachments/" prefix so the lookup
                    # resolves inside the "attachments" container regardless of
                    # which intake source produced the message.
                    normalized_path = blob_path.lstrip("/") if blob_path else blob_path
                    if normalized_path and normalized_path.startswith("attachments/"):
                        normalized_path = normalized_path[len("attachments/"):]
                    filename = normalized_path.split("/")[-1] if normalized_path and "/" in normalized_path else normalized_path
                    try:
                        blob_client = container_client.get_blob_client(normalized_path)
                        download = await blob_client.download_blob()
                        content_bytes = await download.readall()
                        logger.info(f"Downloaded blob: {normalized_path} (original: {blob_path}, {len(content_bytes)} bytes)")

                        extracted = await self.doc_intel_tool.analyze_document_from_bytes(
                            document_bytes=content_bytes,
                            filename=filename,
                        )
                        # Surface Document Intelligence failures loudly — do NOT
                        # let an empty/failed extraction silently degrade the
                        # downstream extractor into producing all-Unknown events.
                        if not extracted.get("success", False):
                            di_error = extracted.get("error", "unknown DI failure")
                            logger.error(
                                f"Document Intelligence FAILED for {blob_path}: {di_error}"
                            )
                            if email_data is not None:
                                email_data.setdefault("_attachment_processing_errors", []).append({
                                    "path": blob_path,
                                    "filename": filename,
                                    "error": f"document_intelligence_failed: {di_error}",
                                    "error_type": "DocumentIntelligenceFailure",
                                    "stage": "di_extract",
                                })
                        elif not (extracted.get("content") or "").strip():
                            logger.error(
                                f"Document Intelligence returned EMPTY content for {blob_path}"
                            )
                            if email_data is not None:
                                email_data.setdefault("_attachment_processing_errors", []).append({
                                    "path": blob_path,
                                    "filename": filename,
                                    "error": "document_intelligence_returned_empty_text",
                                    "error_type": "EmptyExtraction",
                                    "stage": "di_extract",
                                })
                        results.append({
                            "name": filename,
                            "size": len(content_bytes),
                            "extracted_content": extracted,
                        })
                    except Exception as e:
                        logger.error(f"Error processing blob {blob_path}: {e}", exc_info=True)
                        if email_data is not None:
                            email_data.setdefault("_attachment_processing_errors", []).append({
                                "path": blob_path,
                                "filename": filename,
                                "error": str(e),
                                "error_type": type(e).__name__,
                                "stage": "blob_download",
                            })
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
            attachment_summary += self._get_extracted_text(att)[:1500]
            
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

    async def _extract_document_events(self, attachment_analysis: list, initial_category: str = "Unknown") -> list:
        """
        Extract per-document PE event attributes using the LLM.

        Called in triage-only mode where full classification is skipped but we
        still need per-document event data for dedup and dashboard counts.

        Args:
            attachment_analysis: List of processed attachment results from
                _process_attachments, each with {name, size, extracted_content}.
            initial_category: The initial category from relevance check as hint.

        Returns:
            List of per-document event dicts with {document_name, category,
            pe_company, fund_name, investor, amount, due_date, confidence}.
        """
        if not attachment_analysis:
            return []

        events = []
        for att in attachment_analysis:
            event = await self._extract_single_document_event(att, initial_category)
            events.append(event)

        logger.info(f"Extracted {len(events)} per-document events for {len(attachment_analysis)} attachment(s)")
        return events

    async def _extract_single_document_event(self, attachment: dict, initial_category: str) -> dict:
        """Extract one PE event for one attachment, then enrich it deterministically."""
        # Hard-fail loudly when upstream extraction failed or returned no text.
        # Producing an all-Unknown event in this case is the silent-failure mode
        # the user explicitly called out as bad design — surface it instead.
        extracted_content = attachment.get("extracted_content") or {}
        di_failed = extracted_content.get("success") is False
        text = self._get_extracted_text(attachment)
        if di_failed or not text.strip():
            name = attachment.get("name", "unknown")
            di_error = (
                extracted_content.get("error")
                if di_failed
                else "empty_document_text"
            )
            logger.error(
                f"Cannot extract PE event for {name}: "
                f"{'di_failed' if di_failed else 'no text'} ({di_error})"
            )
            err_tag = (
                f"di_extraction_failed: {di_error}" if di_failed
                else "empty_document_text"
            )
            failed_event = {
                "document_name": name,
                "category": initial_category or "Unknown",
                "pe_company": None,
                "fund_name": None,
                "investor": None,
                "amount": None,
                "due_date": None,
                "confidence": 0.0,
                "extraction_method": "failed",
                "validation_errors": [err_tag],
            }
            content_hash = attachment.get("contentMd5") or attachment.get("content_md5")
            if content_hash:
                failed_event["content_hash"] = content_hash
            return failed_event

        deterministic_event = self._extract_deterministic_document_event(attachment, initial_category)
        llm_event = None

        if self._is_deterministic_event_complete(deterministic_event):
            logger.info(
                "Deterministic extraction completed %s: category=%s fund=%s investor=%s amount=%s due_date=%s",
                deterministic_event.get("document_name"),
                deterministic_event.get("category"),
                deterministic_event.get("fund_name"),
                deterministic_event.get("investor"),
                deterministic_event.get("amount"),
                deterministic_event.get("due_date"),
            )
            deterministic_event.pop("_source_text", None)
            return deterministic_event

        try:
            if not getattr(self, "_doc_events_agent", None):
                self._doc_events_agent = self._create_doc_events_agent()

            documents_text = self._build_document_events_text(attachment)
            user_prompt = DOCUMENT_EVENTS_USER_PROMPT.format(documents_text=documents_text)
            run = self.agents_client.create_thread_and_process_run(
                agent_id=self._doc_events_agent.id,
                thread={"messages": [{"role": "user", "content": user_prompt}]}
            )

            messages = self.agents_client.messages.list(thread_id=run.thread_id)
            for msg in messages:
                if msg.role == "assistant":
                    for content in msg.content:
                        if hasattr(content, 'text'):
                            parsed = self._extract_json_from_response(content.text.value)
                            if parsed and parsed.get("pe_events"):
                                llm_event = parsed["pe_events"][0]
                                break
                    if llm_event:
                        break
        except Exception as e:
            logger.warning(
                f"LLM document extraction failed for {attachment.get('name', 'unknown')}: {e}",
                exc_info=True,
            )
            llm_event = {"validation_errors": [f"llm_extraction_failed: {e}"]}

        return self._merge_document_events(deterministic_event, llm_event or {})

    def _build_document_events_text(self, attachment: dict) -> str:
        """Build the document-event extraction prompt text for a single attachment."""
        extracted = attachment.get("extracted_content", {})
        docs_text = f"\n### Document: {attachment.get('name', 'unknown')}\n"
        docs_text += f"Pages: {extracted.get('page_count', 0)}\n"
        docs_text += f"\n**Text content:**\n"
        docs_text += self._get_extracted_text(attachment)[:6000]

        tables = extracted.get("tables", [])
        if tables:
            docs_text += f"\n\n**Tables ({len(tables)} found):**\n"
            for i, table in enumerate(tables[:5]):
                docs_text += f"\nTable {i+1} ({table.get('row_count')}x{table.get('column_count')}):\n"
                for row in table.get("rows", [])[:10]:
                    docs_text += " | ".join(str(cell)[:50] for cell in row) + "\n"

        return docs_text + "\n---\n"

    def _extract_deterministic_document_event(self, attachment: dict, initial_category: str = "Unknown") -> dict:
        """Extract obvious labelled PE event fields from one document without using an LLM."""
        name = attachment.get("name", "unknown")
        text = self._get_extracted_text(attachment)
        combined_text = f"{name}\n{text}"
        category = self._infer_document_category(name, text) or initial_category or "Unknown"
        fields = self._extract_common_document_fields(text)

        event = {
            "document_name": name,
            "category": category,
            "pe_company": fields.get("pe_company"),
            "fund_name": fields.get("fund_name"),
            "investor": fields.get("investor"),
            "amount": fields.get("amount"),
            "due_date": fields.get("due_date"),
            "confidence": 0.75 if fields else 0.5,
            "extraction_method": "deterministic_labels",
        }
        event.update({key: value for key, value in fields.items() if value is not None})

        content_hash = attachment.get("contentMd5") or attachment.get("content_md5")
        if content_hash:
            event["content_hash"] = content_hash

        if not self._has_meaningful_value(event.get("fund_name")):
            inferred_fund = self._infer_fund_name_from_text(combined_text, category)
            if inferred_fund:
                event["fund_name"] = inferred_fund

        # Stash the source text on the event so the merge step can ground LLM
        # values against it. Stripped before persistence in `_merge_document_events`.
        event["_source_text"] = combined_text

        return event

    def _get_extracted_text(self, attachment: dict) -> str:
        """Return document text regardless of the exact extraction payload shape."""
        extracted = attachment.get("extracted_content") or {}
        candidates = [
            extracted.get("full_text"),
            extracted.get("content"),
            extracted.get("text"),
            attachment.get("full_text"),
            attachment.get("content"),
            attachment.get("text"),
        ]
        summary = extracted.get("summary") if isinstance(extracted, dict) else None
        if isinstance(summary, dict):
            candidates.append(summary.get("first_500_chars"))

        return "\n".join(str(value) for value in candidates if self._has_meaningful_value(value))

    def _is_deterministic_event_complete(self, event: dict) -> bool:
        """Return True when deterministic extraction has enough fields to skip LLM extraction."""
        category = event.get("category")
        if category == "Capital Call":
            return not self._validate_document_event(event)
        return all(
            self._has_meaningful_value(event.get(field))
            for field in ["category", "fund_name", "investor"]
        )

    def _extract_common_document_fields(self, text: str) -> dict:
        """Extract labelled fields that commonly appear in PE notices."""
        fields = {
            "notice_date": self._extract_notice_date(text),
            "investor": self._extract_label_value(text, ["Investor Name", "Investor", "Limited Partner", "LP Name"]),
            "share_class": self._extract_label_value(text, ["Share Class"]),
            "currency": self._extract_label_value(text, ["Currency"]),
            "total_commitment": self._extract_money_label(text, ["Total Commitment"]),
            "capital_called_with_notice": self._extract_money_label(text, ["Capital called with this notice"]),
            "fund_level_amount_called": self._extract_money_label(text, ["Fund-level amount called", "Fund level amount called"]),
            "investor_level_amount_called": self._extract_money_label(text, ["Investor-level amount called", "Investor level amount called"]),
            "total_amount_due": self._extract_money_label(text, ["Total Amount Due", "Amount Due", "Total Due"]),
            "relevant_amount": self._extract_money_label(text, ["Relevant Amount"]),
            "value_date": self._extract_date_label(text, ["Value date", "Payment date", "Due date", "Settlement date"]),
            "effective_date": self._extract_date_label(text, ["Effective Date", "Effective date"]),
            "closing_date": self._extract_closing_date(text),
            "reference": self._extract_label_value(text, ["Reference", "Payment Reference"]),
        }

        fields["due_date"] = (
            fields.get("value_date")
            or fields.get("effective_date")
            or fields.get("closing_date")
        )
        fields["amount"] = (
            fields.get("total_amount_due")
            or fields.get("relevant_amount")
            or fields.get("investor_level_amount_called")
            or fields.get("capital_called_with_notice")
            or fields.get("fund_level_amount_called")
        )
        fields["fund_name"] = self._extract_fund_name(text)
        fields["pe_company"] = self._infer_pe_company(fields.get("fund_name"))
        return {key: value for key, value in fields.items() if self._has_meaningful_value(value)}

    def _merge_document_events(self, deterministic_event: dict, llm_event: dict) -> dict:
        """Merge LLM and deterministic extraction, preferring deterministic labelled values.

        After merging, every string-valued field is grounded against the source text:
        if a value does not appear in the document, it is dropped (set to None) and
        recorded in `validation_errors` so downstream code can flag the record as
        needing attention rather than persist a hallucinated value.
        """
        merged = dict(llm_event or {})
        for key, value in deterministic_event.items():
            if self._has_meaningful_value(value) or not self._has_meaningful_value(merged.get(key)):
                merged[key] = value

        if not self._has_meaningful_value(merged.get("document_name")):
            merged["document_name"] = deterministic_event.get("document_name", "unknown")

        # Ground every extracted string against the source text. Only deterministic
        # values (which came from regex on the source) are trusted unconditionally.
        source_text = deterministic_event.get("_source_text") or ""
        if source_text:
            self._ground_event_against_source(merged, deterministic_event, source_text)

        # Strip the private source-text marker before persisting / returning.
        merged.pop("_source_text", None)

        validation_errors = self._validate_document_event(merged)
        # Preserve any grounding errors recorded above; do not overwrite them.
        if validation_errors or merged.get("validation_errors"):
            existing = list(merged.get("validation_errors") or [])
            for tag in validation_errors:
                if tag not in existing:
                    existing.append(tag)
            if existing:
                merged["validation_errors"] = existing
                merged["confidence"] = min(float(merged.get("confidence") or 0.5), 0.6)
        return merged

    # Fields that must be grounded in the source text (string-valued document content).
    _GROUNDED_FIELDS = (
        "investor",
        "fund_name",
        "pe_company",
        "currency",
        "share_class",
        "reference",
        "total_commitment",
        "capital_called_with_notice",
        "fund_level_amount_called",
        "investor_level_amount_called",
        "total_amount_due",
        "amount",
    )

    def _ground_event_against_source(self, merged: dict, deterministic_event: dict, source_text: str) -> None:
        """Drop any merged field that is not actually present in the source text.

        Values that came from the deterministic regex extractor are trusted as-is
        because they were already pulled from the source. Values that came from
        the LLM (and don't match a deterministic value) must appear in the source
        text or they are discarded as hallucinations.
        """
        normalized_source = self._normalize_for_grounding(source_text)
        ungrounded = []
        for field in self._GROUNDED_FIELDS:
            value = merged.get(field)
            if not self._has_meaningful_value(value):
                continue
            # Trust deterministic regex output unchanged.
            if deterministic_event.get(field) == value:
                continue
            if self._value_appears_in_source(value, normalized_source):
                continue
            ungrounded.append(field)
            merged[field] = None

        if ungrounded:
            existing = list(merged.get("validation_errors") or [])
            for field in ungrounded:
                tag = f"ungrounded_{field}"
                if tag not in existing:
                    existing.append(tag)
            merged["validation_errors"] = existing

    @staticmethod
    def _normalize_for_grounding(text: str) -> str:
        """Lowercase and collapse whitespace/punctuation for substring grounding checks."""
        lowered = text.lower()
        return re.sub(r"[\s,.\-/_]+", " ", lowered).strip()

    def _value_appears_in_source(self, value, normalized_source: str) -> bool:
        """True if the (normalized) value appears as a substring of the normalized source.

        For amount-like strings ("305400.00 EUR") we also try the digit-only form.
        """
        s = self._normalize_for_grounding(str(value))
        if not s:
            return False
        if s in normalized_source:
            return True
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if digits and digits in re.sub(r"[^0-9]", "", normalized_source):
            return True
        return False

    def _validate_document_event(self, event: dict) -> list[str]:
        """Return validation gaps for event types where key fields are expected."""
        category = event.get("category")
        errors = []
        if category == "Capital Call":
            if not self._has_meaningful_value(event.get("fund_name")):
                errors.append("missing_fund_name")
            if not self._has_meaningful_value(event.get("investor")):
                errors.append("missing_investor")
            if not self._has_meaningful_value(event.get("amount")):
                errors.append("missing_amount")
            if not self._has_meaningful_value(event.get("due_date")):
                errors.append("missing_due_date")
        return errors

    def _infer_document_category(self, document_name: str, text: str) -> str | None:
        """Infer PE event category from attachment name and text."""
        source = f"{document_name}\n{text}".lower()
        for category, keywords in EVENT_CATEGORY_KEYWORDS:
            if any(keyword in source for keyword in keywords):
                return category
        return None

    def _extract_label_value(self, text: str, labels: list[str]) -> str | None:
        """Extract the text following one of the provided labels.

        Matches a label anywhere on a line (Document Intelligence often joins
        two labelled fields onto a single line, e.g. ``Relevant Amount: 178.51
        EUR Effective Date: 05/03/2026``). Capture stops at the next
        ``<Capitalized Words>:`` label on the same line.
        """
        next_label_re = re.compile(r"\s+(?:[A-Z][A-Za-z0-9]*[- ]?){1,4}:\s")
        for label in labels:
            pattern = rf"(?i)\b{re.escape(label)}\s*:\s*([^\r\n]+)"
            match = re.search(pattern, text)
            if match:
                value = match.group(1).strip()
                cut = next_label_re.search(value)
                if cut:
                    value = value[: cut.start()].strip()
                return value or None
        return None

    def _extract_money_label(self, text: str, labels: list[str]) -> str | None:
        value = self._extract_label_value(text, labels)
        return self._normalize_money(value) if value else None

    def _extract_date_label(self, text: str, labels: list[str]) -> str | None:
        value = self._extract_label_value(text, labels)
        return self._normalize_date(value) if value else None

    def _extract_notice_date(self, text: str) -> str | None:
        # Match "<City>, DD/MM/YYYY" at the start of a line; allow trailing
        # content (Document Intelligence sometimes joins the city/date with the
        # fund header on the same line).
        match = re.search(r"(?im)^\s*[A-Za-zÀ-ÿ .'-]+,\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", text)
        return self._normalize_date(match.group(1)) if match else None

    def _extract_closing_date(self, text: str) -> str | None:
        match = re.search(r"(?i)closing\s*#?\s*\d*\s*\((\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\)", text)
        return self._normalize_date(match.group(1)) if match else None

    def _extract_fund_name(self, text: str) -> str | None:
        labels_value = self._extract_label_value(text, ["Fund", "Fund Name"])
        if labels_value:
            return self._clean_fund_name(labels_value)

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines[:12]:
            if re.search(r"(?i)\bfund\b", line) and not re.search(
                r"(?i)(amount|reference|commitment|level)", line
            ):
                cleaned = self._clean_fund_name(line)
                if cleaned:
                    return cleaned
        return None

    def _clean_fund_name(self, raw: str) -> str | None:
        """Strip city/date prefix and document-type suffix from a fund header.

        Handles inputs like ``Munich, 20/02/2026 ALPINE GROWTH PARTNERS FUND I -
        REDISTRIBUTION NOTICE #1`` -> ``Alpine Growth Partners Fund I``.
        """
        if not raw:
            return None
        cleaned = raw.strip()
        # Drop leading ``<City>, DD/MM/YYYY`` prefix.
        cleaned = re.sub(
            r"^[A-Za-zÀ-ÿ .'-]+,\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s+",
            "",
            cleaned,
        )
        # Drop trailing document-type suffix (capital call, distribution,
        # redistribution, tax statement, etc.) and anything after it.
        cleaned = re.sub(
            r"(?i)\s*[-–]?\s*(capital\s+call|redistribution|distribution|tax\s+statement|tax)\s+notice\b.*$",
            "",
            cleaned,
        )
        cleaned = re.sub(r"(?i)\s*[-–]?\s*tax\s+statement\b.*$", "", cleaned)
        cleaned = cleaned.strip(" -–")
        if not cleaned:
            return None
        # Title-case if the value is all caps so we get "Alpine Growth Partners
        # Fund I" instead of "ALPINE GROWTH PARTNERS FUND I". The roman numeral
        # is preserved by re-capitalising trailing single letters.
        if cleaned.isupper():
            titled = cleaned.title()
            titled = re.sub(
                r"\b(I{1,3}|IV|V|VI{0,3}|IX|X)\b",
                lambda m: m.group(1).upper(),
                titled,
                flags=re.IGNORECASE,
            )
            return titled
        return cleaned

    def _infer_fund_name_from_text(self, text: str, category: str) -> str | None:
        if category != "Capital Call":
            return None
        return self._extract_fund_name(text)

    def _infer_pe_company(self, fund_name: str | None) -> str | None:
        if not fund_name:
            return None
        match = re.match(r"(.+?)\s+Fund\b", fund_name, flags=re.IGNORECASE)
        return match.group(1).strip() if match else None

    def _normalize_money(self, value: str | None) -> str | None:
        if not value:
            return None
        currency_match = re.search(r"\b([A-Z]{3})\b", value)
        amount_match = re.search(r"([0-9][0-9\s,.]*)", value)
        if not amount_match:
            return value.strip()
        number = amount_match.group(1).replace(" ", "").replace(",", "")
        currency = currency_match.group(1) if currency_match else ""
        return f"{number} {currency}".strip()

    def _normalize_date(self, value: str | None) -> str | None:
        if not value:
            return None
        match = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", value)
        if not match:
            return value.strip()
        day, month, year = match.groups()
        if len(year) == 2:
            year = f"20{year}"
        return f"{year}-{int(month):02d}-{int(day):02d}"

    def _has_meaningful_value(self, value) -> bool:
        if value is None:
            return False
        return str(value).strip().lower() not in MISSING_FIELD_VALUES

    def cleanup(self):
        """Clean up agent resources."""
        if self._relevance_agent:
            try:
                self.agents_client.delete_agent(self._relevance_agent.id)
            except Exception as e:
                logger.warning(f"Failed to delete relevance agent: {e}", exc_info=True)

        if self._classification_agent:
            try:
                self.agents_client.delete_agent(self._classification_agent.id)
            except Exception as e:
                logger.warning(f"Failed to delete classification agent: {e}", exc_info=True)

        if self._doc_events_agent:
            try:
                self.agents_client.delete_agent(self._doc_events_agent.id)
            except Exception as e:
                logger.warning(f"Failed to delete doc-events agent: {e}", exc_info=True)


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
                logger.error(f"Error in processing loop: {e}", exc_info=True)
                await asyncio.sleep(5)  # Brief pause before retry
                
    finally:
        agent.cleanup()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_agent_loop())
