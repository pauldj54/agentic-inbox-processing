"""
Cosmos DB Tools for storing email classification results and extracted data.
Uses DefaultAzureCredential for passwordless authentication.
"""

import os
import json
import hashlib
import logging
from typing import Optional, List, Tuple, Union
from datetime import datetime
from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.aio import CosmosClient as AsyncCosmosClient

logger = logging.getLogger(__name__)


def _extract_sender_domain(from_field: str) -> str:
    """Extract domain from an email 'from' field."""
    if not from_field:
        return "unknown"
    if "<" in from_field and ">" in from_field:
        from_field = from_field.split("<")[1].split(">")[0]
    if "@" in from_field:
        return from_field.split("@")[1].strip().lower()
    return from_field.strip().lower() or "unknown"


def _compute_email_partition_key(from_field: str, received_at: str) -> str:
    """Compute partitionKey = {sender_domain}_{YYYY-MM} for an email record."""
    domain = _extract_sender_domain(from_field)
    if received_at:
        try:
            dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
            month = dt.strftime("%Y-%m")
        except (ValueError, TypeError):
            month = datetime.utcnow().strftime("%Y-%m")
    else:
        month = datetime.utcnow().strftime("%Y-%m")
    return f"{domain}_{month}"


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


class CosmosDBTools:
    """Tools for interacting with Azure Cosmos DB."""
    
    # Container names
    CONTAINER_INTAKE_RECORDS = "intake-records"
    CONTAINER_PE_EVENTS = "pe-events"
    CONTAINER_CLASSIFICATIONS = "classifications"
    CONTAINER_AUDIT_LOGS = "audit-logs"
    CONTAINER_EXTRACTED_DATA = "extracted-data"
    
    def __init__(
        self,
        endpoint: Optional[str] = None,
        database_name: Optional[str] = None
    ):
        """
        Initialize the Cosmos DB client.
        
        Args:
            endpoint: Cosmos DB endpoint URL
            database_name: Name of the database
        """
        self.endpoint = endpoint or os.environ.get("COSMOS_ENDPOINT")
        self.database_name = database_name or os.environ.get("COSMOS_DATABASE", "email-processing")
        
        if not self.endpoint:
            raise ValueError(
                "Cosmos DB endpoint is required. "
                "Set COSMOS_ENDPOINT environment variable."
            )
        
        self.credential = DefaultAzureCredential()
    
    def _get_sync_client(self) -> CosmosClient:
        """Get synchronous Cosmos DB client."""
        return CosmosClient(
            url=self.endpoint,
            credential=self.credential
        )
    
    def _get_async_client(self) -> AsyncCosmosClient:
        """Get asynchronous Cosmos DB client."""
        from azure.identity.aio import DefaultAzureCredential as AsyncCredential
        return AsyncCosmosClient(
            url=self.endpoint,
            credential=AsyncCredential()
        )

    def get_email_document(self, email_id: str) -> dict | None:
        """Fetch an email document from Cosmos DB by ID.

        Args:
            email_id: The unique email ID (Graph API message ID).

        Returns:
            The email document dict, or None if not found.
        """
        with self._get_sync_client() as client:
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.CONTAINER_INTAKE_RECORDS)
            query = "SELECT * FROM c WHERE c.id = @emailId OR c.emailId = @emailId"
            items = list(container.query_items(
                query=query,
                parameters=[{"name": "@emailId", "value": email_id}],
                enable_cross_partition_query=True,
            ))
            if items:
                return items[0]
            return None

    def find_by_content_hash(
        self,
        content_hash: str,
        partition_key: str,
    ) -> dict | None:
        """Find an existing intake record by contentHash within a partition.

        Args:
            content_hash: Base64-encoded MD5 hash to search for.
            partition_key: Cosmos DB partition key ({domain}_{YYYY-MM}).

        Returns:
            The matching record dict, or None if no match found.
        """
        if not content_hash:
            return None

        with self._get_sync_client() as client:
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.CONTAINER_INTAKE_RECORDS)
            query = "SELECT TOP 1 * FROM c WHERE c.contentHash = @contentHash"
            items = list(container.query_items(
                query=query,
                parameters=[{"name": "@contentHash", "value": content_hash}],
                partition_key=partition_key,
            ))
            if items:
                logger.info(
                    f"Found existing record by contentHash: id={items[0].get('id', '')[:20]}..., "
                    f"deliveryCount={items[0].get('deliveryCount', 1)}"
                )
                return items[0]
            return None

    def find_by_filename(self, filename: str, partition_key: str) -> dict | None:
        """Find an existing intake record with a matching attachment filename in the given partition.

        Args:
            filename: Original filename to search for in attachmentPaths.
            partition_key: Cosmos DB partition key ({domain}_{YYYY-MM}).

        Returns:
            The matching record dict, or None if no match found.
        """
        if not filename:
            return None

        with self._get_sync_client() as client:
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.CONTAINER_INTAKE_RECORDS)
            query = (
                "SELECT TOP 1 * FROM c "
                "WHERE ARRAY_CONTAINS(c.attachmentPaths, {\"originalName\": @name}, true)"
            )
            items = list(container.query_items(
                query=query,
                parameters=[{"name": "@name", "value": filename}],
                partition_key=partition_key,
            ))
            if items:
                logger.info(
                    f"Found existing record by filename: id={items[0].get('id', '')[:20]}..., "
                    f"filename={filename}"
                )
                return items[0]
            return None

    def increment_delivery_count(
        self,
        record: dict,
        content_hash: str,
        action: str = "duplicate",
    ) -> dict:
        """Increment deliveryCount on an existing record and append to deliveryHistory.

        Args:
            record: The existing Cosmos DB record to update.
            content_hash: Content hash of the new delivery.
            action: Delivery action type ("duplicate" or "update").

        Returns:
            The updated record.
        """
        record["deliveryCount"] = record.get("deliveryCount", 1) + 1
        record["lastDeliveredAt"] = datetime.utcnow().isoformat()
        history = record.get("deliveryHistory", [])
        history.append({
            "deliveredAt": datetime.utcnow().isoformat(),
            "contentHash": content_hash,
            "action": action,
        })
        record["deliveryHistory"] = history
        if action == "update":
            record["version"] = record.get("version", 1) + 1
            record["contentHash"] = content_hash
        record["updatedAt"] = datetime.utcnow().isoformat()

        with self._get_sync_client() as client:
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.CONTAINER_INTAKE_RECORDS)
            result = container.upsert_item(record)
            logger.info(
                f"Delivery tracking updated: id={record.get('id', '')[:20]}..., "
                f"action={action}, deliveryCount={record['deliveryCount']}, "
                f"version={record.get('version', 1)}"
            )
            return result

    def update_email_classification(
        self,
        email_id: str,
        classification: str,
        confidence_score: float,
        classification_details: dict,
        step: str = "final",
        email_data: dict = None
    ) -> dict:
        """
        Update an email document with classification results.
        Creates a new document if one doesn't exist (for direct queue testing).
        
        Args:
            email_id: The unique email ID
            classification: Category assigned
            confidence_score: Confidence level (0.0 to 1.0)
            classification_details: Additional metadata
            step: Classification step ("relevance" or "final")
            email_data: Original email data (used to create document if not found)
            
        Returns:
            Updated document
        """
        logger.info(f"Updating classification for email {email_id[:20]}...")
        
        with self._get_sync_client() as client:
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.CONTAINER_INTAKE_RECORDS)
            
            # Try to find the email document by id or emailId
            # The Logic App creates documents with id=emailId, so we check both
            query = "SELECT * FROM c WHERE c.id = @emailId OR c.emailId = @emailId"
            items = list(container.query_items(
                query=query,
                parameters=[{"name": "@emailId", "value": email_id}],
                enable_cross_partition_query=True
            ))
            
            # Debug: Log all found documents
            logger.debug(f"Query found {len(items)} document(s) for email {email_id[:20]}...")
            for item in items:
                logger.debug(f"  - status={item.get('status')}, id={item.get('id')[:20]}...")
            
            if not items:
                # Document not found - create a new one from email_data if available
                if email_data:
                    logger.info(f"Creating new email document for {email_id[:20]}...")
                    # Get attachment count
                    att_count = email_data.get("attachmentsCount", 0)
                    doc = {
                        "id": email_id,
                        "emailId": email_id,
                        "from": email_data.get("from", email_data.get("sender", "unknown")),
                        "subject": email_data.get("subject", ""),
                        "emailBody": email_data.get("emailBody", email_data.get("bodyText", email_data.get("body", ""))),
                        "receivedAt": email_data.get("receivedAt", email_data.get("received_at", datetime.utcnow().isoformat())),
                        "hasAttachments": parse_bool(email_data.get("hasAttachments", email_data.get("has_attachments", False))),
                        "attachmentsCount": int(att_count) if att_count else 0,
                        "attachmentPaths": email_data.get("attachmentPaths", []),
                        "rejectedAttachments": email_data.get("rejectedAttachments", []),
                        "downloadFailures": [],  # Scaffold — populated by US2/T013
                        "intakeSource": "email",
                        "status": "received",
                        "createdAt": datetime.utcnow().isoformat()
                    }
                    # Compute partitionKey = {sender_domain}_{YYYY-MM}
                    doc["partitionKey"] = _compute_email_partition_key(
                        doc["from"], doc["receivedAt"]
                    )
                    # Persist link download metadata if available
                    link_result = email_data.get("_link_download_result")
                    if link_result:
                        doc["linkDownload"] = link_result
                        doc["downloadFailures"] = link_result.get("failures", [])
                else:
                    logger.warning(f"Email document not found for {email_id[:20]}... and no email_data provided. Cannot update.")
                    return None
            else:
                doc = items[0]
                # Ensure doc has required fields from email_data if missing
                if email_data:
                    if not doc.get("from"):
                        doc["from"] = email_data.get("from", email_data.get("sender", "unknown"))
                    if not doc.get("subject"):
                        doc["subject"] = email_data.get("subject", "")
                    if not doc.get("emailBody"):
                        doc["emailBody"] = email_data.get("emailBody", email_data.get("bodyText", ""))
                    if not doc.get("receivedAt"):
                        doc["receivedAt"] = email_data.get("receivedAt", datetime.utcnow().isoformat())
                    if not doc.get("status"):
                        doc["status"] = "received"
                    # Update attachment info ONLY if email_data has valid values, otherwise preserve existing
                    email_att_count = email_data.get("attachmentsCount", 0)
                    if email_att_count:
                        doc["attachmentsCount"] = int(email_att_count)
                        doc["hasAttachments"] = int(email_att_count) > 0
                    elif not doc.get("attachmentsCount"):
                        # Only set to 0 if document doesn't already have a value
                        doc["hasAttachments"] = parse_bool(email_data.get("hasAttachments", False))
                        doc["attachmentsCount"] = 0
                    # Persist enriched attachmentPaths (includes link-sourced entries)
                    enriched_paths = email_data.get("attachmentPaths")
                    if enriched_paths is not None:
                        doc["attachmentPaths"] = enriched_paths
                    # Persist link download metadata and scaffold downloadFailures
                    link_result = email_data.get("_link_download_result")
                    if link_result:
                        doc["linkDownload"] = link_result
                        doc["downloadFailures"] = link_result.get("failures", [])
                    elif "downloadFailures" not in doc:
                        doc["downloadFailures"] = []  # Scaffold empty array
                    # Preserve rejectedAttachments written by the Logic App
                    if "rejectedAttachments" not in doc:
                        doc["rejectedAttachments"] = []
            
            # Update classification fields
            if step == "relevance":
                doc["relevanceCheck"] = {
                    "isRelevant": classification != "Others",
                    "initialCategory": classification,
                    "confidence": confidence_score,
                    "reasoning": classification_details.get("reasoning", ""),
                    "checkedAt": datetime.utcnow().isoformat()
                }
            else:
                # Embedded classification with fund_name and pe_company
                doc["classification"] = {
                    "category": classification,
                    "confidence": confidence_score,
                    "fund_name": classification_details.get("fund_name", "Unknown"),
                    "pe_company": classification_details.get("pe_company", "Unknown"),
                    "reasoning": classification_details.get("reasoning", ""),
                    "key_evidence": classification_details.get("key_evidence", []),
                    "amount": classification_details.get("amount"),
                    "due_date": classification_details.get("due_date"),
                    "detected_language": classification_details.get("detected_language", "English"),
                    "classifiedAt": datetime.utcnow().isoformat()
                }
                
                # Determine status and queue based on 65% confidence threshold
                pipeline_mode = classification_details.get("pipelineMode", "full")
                if pipeline_mode == "triage-only":
                    doc["status"] = "triaged"
                    doc["queue"] = classification_details.get("targetQueue", os.environ.get("TRIAGE_COMPLETE_QUEUE", "triage-complete"))
                elif classification == "Not PE Related":
                    doc["status"] = "discarded"
                    doc["queue"] = os.environ.get("DISCARDED_QUEUE", "discarded")
                elif confidence_score >= 0.65:
                    doc["status"] = "classified"
                    doc["queue"] = os.environ.get("ARCHIVAL_PENDING_QUEUE", "archival-pending")
                else:
                    doc["status"] = "needs_review"
                    doc["queue"] = os.environ.get("HUMAN_REVIEW_QUEUE", "human-review")

                # Mark processing timestamp when final classification is written
                doc["processedAt"] = datetime.utcnow().isoformat()

            # ── Pipeline mode tracking ──
            if "pipelineMode" in classification_details:
                doc["pipelineMode"] = classification_details["pipelineMode"]
            if "stepsExecuted" in classification_details:
                doc["stepsExecuted"] = classification_details["stepsExecuted"]

            # ── Reconcile hasAttachments / attachmentsCount from actual attachmentPaths ──
            actual_paths = doc.get("attachmentPaths") or []
            doc["attachmentsCount"] = len(actual_paths)
            doc["hasAttachments"] = len(actual_paths) > 0

            doc["updatedAt"] = datetime.utcnow().isoformat()

            # Ensure partitionKey is set (backfill for legacy docs without it)
            if "partitionKey" not in doc:
                doc["partitionKey"] = _compute_email_partition_key(
                    doc.get("from", ""), doc.get("receivedAt", "")
                )
            if "intakeSource" not in doc:
                doc["intakeSource"] = "email"
            
            # Upsert the document (partitionKey is immutable, no delete needed)
            result = container.upsert_item(doc)
            logger.info(f"Updated email document: {email_id[:20]}...")
            
            return result
    
    def store_extracted_content(
        self,
        email_id: str,
        attachment_name: str,
        extracted_content: dict
    ) -> dict:
        """
        Store extracted content from document intelligence.
        
        Args:
            email_id: The email ID this content belongs to
            attachment_name: Name of the attachment file
            extracted_content: The extracted data (text, tables, etc.)
            
        Returns:
            Created document
        """
        logger.info(f"Storing extracted content for {attachment_name}...")
        
        with self._get_sync_client() as client:
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.CONTAINER_EXTRACTED_DATA)
            
            doc = {
                "id": f"{email_id}-{attachment_name}",
                "emailId": email_id,
                "attachmentName": attachment_name,
                "extractedAt": datetime.utcnow().isoformat(),
                "pageCount": extracted_content.get("page_count", 0),
                "tableCount": extracted_content.get("table_count", 0),
                "fullText": extracted_content.get("full_text", ""),
                "tables": extracted_content.get("tables", []),
                "keyValuePairs": extracted_content.get("key_value_pairs", []),
                "summary": extracted_content.get("summary", {})
            }
            
            result = container.upsert_item(doc)
            logger.info(f"Stored extracted content: {doc['id']}")
            
            return result
    
    def store_table_data(
        self,
        email_id: str,
        attachment_name: str,
        table_index: int,
        table_data: dict,
        classification: str
    ) -> dict:
        """
        Store structured table data for querying.
        
        Args:
            email_id: The email ID
            attachment_name: Source attachment name
            table_index: Index of the table in the document
            table_data: Structured table data
            classification: Email classification for context
            
        Returns:
            Created document
        """
        logger.info(f"Storing table {table_index} from {attachment_name}...")
        
        with self._get_sync_client() as client:
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.CONTAINER_EXTRACTED_DATA)
            
            doc = {
                "id": f"{email_id}-{attachment_name}-table-{table_index}",
                "type": "table",
                "emailId": email_id,
                "attachmentName": attachment_name,
                "tableIndex": table_index,
                "classification": classification,
                "extractedAt": datetime.utcnow().isoformat(),
                "rowCount": table_data.get("row_count", 0),
                "columnCount": table_data.get("column_count", 0),
                "rows": table_data.get("rows", []),
                "cells": table_data.get("cells", [])
            }
            
            result = container.upsert_item(doc)
            logger.info(f"Stored table data: {doc['id']}")
            
            return result
    
    def log_classification_event(
        self,
        email_id: str,
        event_type: str,
        details: dict
    ) -> dict:
        """
        Log an audit event for classification tracking.
        
        Args:
            email_id: The email being processed
            event_type: Type of event (e.g., "relevance_check", "classification", "routing")
            details: Event details
            
        Returns:
            Created log entry
        """
        with self._get_sync_client() as client:
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.CONTAINER_AUDIT_LOGS)
            
            doc = {
                "id": f"{email_id}-{event_type}-{datetime.utcnow().timestamp()}",
                "emailId": email_id,
                "eventType": event_type,
                "timestamp": datetime.utcnow().isoformat(),
                "details": details
            }
            
            result = container.upsert_item(doc)
            return result

    def _generate_dedup_key(
        self,
        pe_company: str,
        fund_name: str,
        event_type: str,
        amount: Optional[str] = None,
        due_date: Optional[str] = None,
        investor: Optional[str] = None
    ) -> str:
        """
        Generate a deduplication key for PE events.
        
        The key is a hash of normalized key fields that uniquely identify an event.
        
        Args:
            pe_company: PE firm name
            fund_name: Fund name
            event_type: Type of event (Capital Call, Distribution, etc.)
            amount: Transaction amount (optional)
            due_date: Due date (optional)
            investor: Investor / LP name (optional)
            
        Returns:
            SHA256 hash string (first 16 chars)
        """
        # Normalize fields for consistent matching
        def normalize(s: str) -> str:
            if not s:
                return ""
            # Lowercase, remove extra spaces, remove common suffixes
            s = s.lower().strip()
            s = " ".join(s.split())  # Normalize whitespace
            # Remove common variations
            for suffix in [" llc", " lp", " inc", " corp", " ltd", " partners", " fund"]:
                if s.endswith(suffix):
                    s = s[:-len(suffix)].strip()
            return s
        
        # Extract month from due_date for fuzzy matching (same month = same event)
        date_key = ""
        if due_date:
            try:
                # Handle various date formats
                if "T" in due_date:
                    date_key = due_date[:7]  # YYYY-MM
                elif "-" in due_date:
                    parts = due_date.split("-")
                    if len(parts) >= 2:
                        date_key = f"{parts[0]}-{parts[1]}"
            except:
                date_key = ""
        
        # Normalize amount (remove currency symbols, commas)
        amount_key = ""
        if amount:
            amount_key = "".join(c for c in str(amount) if c.isdigit() or c == ".")
        
        # Build the composite key
        key_parts = [
            normalize(pe_company),
            normalize(fund_name),
            normalize(event_type),
            amount_key,
            date_key,
            normalize(investor or "zava private bank"),
        ]
        
        key_string = "|".join(key_parts)
        hash_value = hashlib.sha256(key_string.encode()).hexdigest()[:16]
        
        logger.debug(f"Generated dedup key: {key_string} -> {hash_value}")
        return hash_value

    def find_or_create_pe_event(
        self,
        email_id: str,
        classification_details: dict,
        intake_source: str = "email",
        received_at: str = None
    ) -> Tuple[dict, bool]:
        """
        Find an existing PE event or create a new one.
        Links the email to the event.
        
        Args:
            email_id: The email ID to link
            classification_details: Classification result with pe_company, fund_name, etc.
            intake_source: "email" or "sftp"
            received_at: ISO timestamp of the source record
            
        Returns:
            Tuple of (pe_event document, is_duplicate boolean)
        """
        pe_company = classification_details.get("pe_company", "Unknown")
        fund_name = classification_details.get("fund_name", "Unknown")
        event_type = classification_details.get("category", "Unknown")
        amount = classification_details.get("amount")
        due_date = classification_details.get("due_date")
        investor = classification_details.get("investor", "Zava Private Bank")
        received_at = received_at or datetime.utcnow().isoformat()
        
        # Generate dedup key
        dedup_key = self._generate_dedup_key(
            pe_company=pe_company,
            fund_name=fund_name,
            event_type=event_type,
            amount=amount,
            due_date=due_date,
            investor=investor,
        )
        
        logger.info(f"Looking for PE event with dedup key: {dedup_key}")
        
        with self._get_sync_client() as client:
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.CONTAINER_PE_EVENTS)
            
            # Try to find existing event by dedup key
            query = "SELECT * FROM c WHERE c.dedupKey = @dedupKey"
            items = list(container.query_items(
                query=query,
                parameters=[{"name": "@dedupKey", "value": dedup_key}],
                enable_cross_partition_query=True
            ))
            
            if items:
                # Found existing event - add email to list
                event = items[0]
                is_duplicate = True
                
                # Add email to the linked emails list if not already there
                if "emailIds" not in event:
                    event["emailIds"] = []
                
                if email_id not in event["emailIds"]:
                    event["emailIds"].append(email_id)
                    event["emailCount"] = len(event["emailIds"])
                    # Append to sourceRecords
                    if "sourceRecords" not in event:
                        event["sourceRecords"] = []
                    event["sourceRecords"].append({
                        "id": email_id, "source": intake_source, "receivedAt": received_at
                    })
                    event["lastEmailAt"] = datetime.utcnow().isoformat()
                    event["updatedAt"] = datetime.utcnow().isoformat()
                    
                    # Update the event
                    container.upsert_item(event)
                    logger.info(f"Added email {email_id[:20]}... to existing PE event (total: {event['emailCount']} emails)")
                else:
                    logger.info(f"Email {email_id[:20]}... already linked to PE event")
                
                return event, is_duplicate
            else:
                # Create new event
                is_duplicate = False
                event_id = f"pe-{dedup_key}-{int(datetime.utcnow().timestamp())}"
                
                event = {
                    "id": event_id,
                    "dedupKey": dedup_key,
                    "eventType": event_type,
                    "peCompany": pe_company,
                    "fundName": fund_name,
                    "investor": investor,
                    "amount": amount,
                    "dueDate": due_date,
                    "emailIds": [email_id],
                    "emailCount": 1,
                    "sourceRecords": [
                        {"id": email_id, "source": intake_source, "receivedAt": received_at}
                    ],
                    "status": "pending",  # pending, archived, reviewed
                    "createdAt": datetime.utcnow().isoformat(),
                    "lastEmailAt": datetime.utcnow().isoformat(),
                    "reasoning": classification_details.get("reasoning", ""),
                    "confidence": classification_details.get("confidence", 0.0),
                    "keyEvidence": classification_details.get("key_evidence", [])
                }
                
                try:
                    result = container.create_item(event)
                    logger.info(f"Created new PE event: {event_id}")
                    return result, is_duplicate
                except Exception as e:
                    # Handle race condition - another process might have created it
                    if "Conflict" in str(e) or "409" in str(e):
                        logger.info("Race condition detected, fetching existing event")
                        items = list(container.query_items(
                            query=query,
                            parameters=[{"name": "@dedupKey", "value": dedup_key}],
                            enable_cross_partition_query=True
                        ))
                        if items:
                            return items[0], True
                    raise

    def mark_email_as_duplicate(
        self,
        email_id: str,
        pe_event_id: str
    ) -> dict:
        """
        Mark an email as a duplicate and link it to the canonical PE event.
        
        Args:
            email_id: The email ID to mark
            pe_event_id: The PE event this email is a duplicate of
            
        Returns:
            Updated email document
        """
        logger.info(f"Marking email {email_id[:20]}... as duplicate of {pe_event_id}")
        
        with self._get_sync_client() as client:
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.CONTAINER_INTAKE_RECORDS)
            
            # Find the email document
            query = "SELECT * FROM c WHERE c.id = @emailId OR c.emailId = @emailId"
            items = list(container.query_items(
                query=query,
                parameters=[{"name": "@emailId", "value": email_id}],
                enable_cross_partition_query=True
            ))
            
            if items:
                doc = items[0]
                doc["isDuplicate"] = True
                doc["peEventId"] = pe_event_id
                doc["updatedAt"] = datetime.utcnow().isoformat()
                
                result = container.upsert_item(doc)
                logger.info(f"Marked email as duplicate: {email_id[:20]}...")
                return result
            else:
                logger.warning(f"Email not found for duplicate marking: {email_id[:20]}...")
                return None

    def get_pe_event_stats(self) -> dict:
        """
        Get statistics about PE events for dashboard display.
        
        Returns:
            Dictionary with event counts by type, duplicate stats, etc.
        """
        with self._get_sync_client() as client:
            database = client.get_database_client(self.database_name)
            container = database.get_container_client(self.CONTAINER_PE_EVENTS)
            
            # Count by event type
            query = """
                SELECT c.eventType, COUNT(1) as count, SUM(c.emailCount) as totalEmails
                FROM c
                GROUP BY c.eventType
            """
            
            stats = {
                "byEventType": {},
                "totalEvents": 0,
                "totalEmails": 0,
                "duplicateEmails": 0
            }
            
            try:
                for item in container.query_items(
                    query=query,
                    enable_cross_partition_query=True
                ):
                    event_type = item.get("eventType", "Unknown")
                    count = item.get("count", 0)
                    total_emails = item.get("totalEmails", 0)
                    
                    stats["byEventType"][event_type] = {
                        "events": count,
                        "emails": total_emails
                    }
                    stats["totalEvents"] += count
                    stats["totalEmails"] += total_emails
                
                # Duplicate emails = total emails - total events
                stats["duplicateEmails"] = stats["totalEmails"] - stats["totalEvents"]
                
            except Exception as e:
                logger.warning(f"Error getting PE event stats: {e}")
            
            return stats


# Tool function definitions for agent framework
def get_cosmos_tool_definitions() -> list:
    """
    Returns the tool definitions for the Azure AI Agent framework.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "update_email_classification",
                "description": (
                    "Updates an email document in Cosmos DB with classification results. "
                    "Call this after determining the email category and confidence score."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "email_id": {
                            "type": "string",
                            "description": "The unique email ID"
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
                        "confidence_score": {
                            "type": "number",
                            "description": "Classification confidence score between 0.0 and 1.0"
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Explanation for the classification decision"
                        },
                        "step": {
                            "type": "string",
                            "description": "Classification step: 'relevance' for initial check, 'final' for full classification",
                            "enum": ["relevance", "final"]
                        }
                    },
                    "required": ["email_id", "classification", "confidence_score", "reasoning", "step"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "store_extracted_content",
                "description": (
                    "Stores content extracted from PDF attachments (text, tables, key-value pairs) "
                    "to Cosmos DB for later querying and analysis."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "email_id": {
                            "type": "string",
                            "description": "The email ID this content belongs to"
                        },
                        "attachment_name": {
                            "type": "string",
                            "description": "Name of the PDF attachment"
                        },
                        "page_count": {
                            "type": "integer",
                            "description": "Number of pages in the document"
                        },
                        "table_count": {
                            "type": "integer",
                            "description": "Number of tables found"
                        },
                        "text_summary": {
                            "type": "string",
                            "description": "First 500 characters of extracted text"
                        }
                    },
                    "required": ["email_id", "attachment_name"]
                }
            }
        }
    ]
