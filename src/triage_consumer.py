"""
Triage Consumer Client

Continuously listens to the triage-complete queue and processes incoming messages.
For each document, it:
1. Extracts and displays relevant information
2. Calls the document processing API
3. Acknowledges the message

Usage:
    python src/triage_consumer.py
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient
import requests

# Load environment variables
env_path = Path(__file__).parent.parent / ".env01"
load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
SERVICEBUS_NAMESPACE = os.environ.get("SERVICEBUS_NAMESPACE")
TRIAGE_QUEUE = os.environ.get("TRIAGE_COMPLETE_QUEUE", "triage-complete")
TRIAGE_SB_NAMESPACE = os.environ.get("TRIAGE_COMPLETE_SB_NAMESPACE")

# API Configuration (adjust these to your API endpoint)
API_ENDPOINT = os.environ.get("API_ENDPOINT", "https://your-api-endpoint.com/process")
DATA_MODEL_NAME = os.environ.get("DATA_MODEL_NAME", "Capital Call Statements")
DEFAULT_PROJECT_NAME = os.environ.get("DEFAULT_PROJECT_NAME", "Agentic Inbox Processing")
DEFAULT_ANALYSIS_NAME = os.environ.get("DEFAULT_ANALYSIS_NAME", "Auto-triage Document Processing")
DEFAULT_LANGUAGE = os.environ.get("DEFAULT_LANGUAGE", "en")

# Build fully qualified namespace
namespace = TRIAGE_SB_NAMESPACE or SERVICEBUS_NAMESPACE
if not namespace:
    raise ValueError("SERVICEBUS_NAMESPACE or TRIAGE_COMPLETE_SB_NAMESPACE must be set")

FULLY_QUALIFIED_NAMESPACE = f"{namespace}.servicebus.windows.net"


def format_file_size(size_bytes: Optional[int]) -> str:
    """Format bytes to human-readable size."""
    if not size_bytes:
        return "Unknown"
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def print_message_details(message_data: dict):
    """Print formatted message details to console."""
    
    print("\n" + "="*80)
    print("📄 NEW DOCUMENT RECEIVED")
    print("="*80)
    
    # Email/Document Info
    email_id = message_data.get("emailId", message_data.get("id", "N/A"))
    print(f"\n📧 Document ID: {email_id}")
    print(f"📥 Intake Source: {message_data.get('intakeSource', 'N/A')}")
    
    # Subject/Filename
    if message_data.get("subject"):
        print(f"📌 Subject: {message_data['subject']}")
    if message_data.get("originalFilename"):
        print(f"📄 Original Filename: {message_data['originalFilename']}")
    
    # Sender info (for emails)
    from_info = message_data.get("from", message_data.get("from_name", "N/A"))
    if from_info != "N/A":
        print(f"👤 From: {from_info}")
        from_address = message_data.get("from_address")
        if from_address:
            print(f"✉️  Email: {from_address}")
    
    # Timestamps
    received_at = message_data.get("receivedAt", message_data.get("received_at", "N/A"))
    processed_at = message_data.get("processedAt", message_data.get("processed_date", "N/A"))
    print(f"🕐 Received: {received_at}")
    print(f"⚙️  Processed: {processed_at}")
    
    # Attachments
    attachments = message_data.get("attachmentPaths", [])
    attachment_count = message_data.get("attachmentsCount", len(attachments))
    print(f"\n📎 Attachments: {attachment_count}")
    
    if attachments:
        for idx, att in enumerate(attachments, 1):
            if isinstance(att, dict):
                link = att.get("local_link") or att.get("blobUrl") or att.get("path", "N/A")
                filename = att.get("name", "Attachment")
                size = att.get("size")
                print(f"   {idx}. {filename}")
                print(f"      🔗 Link: {link}")
                if size:
                    print(f"      💾 Size: {format_file_size(size)}")
            else:
                print(f"   {idx}. {att}")
    
    # Blob path (for SFTP files)
    if message_data.get("blobPath"):
        print(f"\n🗂️  Blob Path: {message_data['blobPath']}")
    
    # Relevance info
    relevance = message_data.get("relevance", {})
    if relevance:
        print(f"\n✅ Relevance Score: {relevance.get('confidence', 0.0):.2%}")
        category = relevance.get("initialCategory", "N/A")
        print(f"🏷️  Category: {category}")
        reasoning = relevance.get("reasoning", "")
        if reasoning:
            print(f"💭 Reasoning: {reasoning[:200]}...")
    
    # Pipeline info
    pipeline_mode = message_data.get("pipelineMode", "N/A")
    status = message_data.get("status", "N/A")
    print(f"\n⚙️  Pipeline Mode: {pipeline_mode}")
    print(f"📊 Status: {status}")
    
    # Routing info
    routing = message_data.get("routing", {})
    if routing:
        print(f"🔀 Routing: {routing.get('sourceQueue', 'N/A')} → {routing.get('targetQueue', 'N/A')}")
    
    print("="*80)


def extract_sas_url_from_attachment(attachment: dict, storage_account_url: str) -> Optional[str]:
    """
    Extract or construct a SAS URL from attachment data.
    In production, you'd generate a proper SAS token.
    For now, returns the existing link.
    """
    if isinstance(attachment, dict):
        return (
            attachment.get("local_link") or 
            attachment.get("blobUrl") or 
            attachment.get("path")
        )
    elif isinstance(attachment, str):
        return attachment
    return None


def build_api_request(message_data: dict) -> dict:
    """
    Build API request payload from triage message data.
    
    Args:
        message_data: Triage queue message
        
    Returns:
        API request dictionary
    """
    # Extract documents
    documents = []
    attachments = message_data.get("attachmentPaths", [])
    storage_account_url = os.environ.get("STORAGE_ACCOUNT_URL", "")
    
    for idx, att in enumerate(attachments, 1):
        sas_url = extract_sas_url_from_attachment(att, storage_account_url)
        if sas_url:
            # Extract document name
            doc_name = f"Document_{idx}"
            if isinstance(att, dict):
                doc_name = att.get("name", doc_name)
            elif message_data.get("originalFilename"):
                doc_name = message_data["originalFilename"]
            elif message_data.get("subject"):
                # Use subject as fallback for email attachments
                doc_name = f"{message_data['subject']}_attachment_{idx}.pdf"
            
            documents.append({
                "sas_url": sas_url,
                "document_name": doc_name
            })
    
    # Extract fund name from subject or body for project_name
    subject = message_data.get("subject", "")
    body = message_data.get("body", "")
    
    # Try to extract fund name (simple heuristic - look for "Fonds" or "Fund")
    project_name = DEFAULT_PROJECT_NAME
    for text in [subject, body]:
        if "fonds" in text.lower():
            # Extract the first occurrence of "...Fonds..."
            words = text.split()
            for i, word in enumerate(words):
                if "fonds" in word.lower():
                    # Get surrounding words
                    start = max(0, i-2)
                    end = min(len(words), i+3)
                    project_name = " ".join(words[start:end])
                    break
            break
        elif "fund" in text.lower():
            words = text.split()
            for i, word in enumerate(words):
                if "fund" in word.lower():
                    start = max(0, i-2)
                    end = min(len(words), i+3)
                    project_name = " ".join(words[start:end])
                    break
            break
    
    # Detect language from relevance or default
    language = DEFAULT_LANGUAGE
    relevance = message_data.get("relevance", {})
    if "french" in str(relevance).lower() or "français" in subject.lower():
        language = "fr"
    
    # Build request
    request = {
        "documents": documents,
        "project_name": project_name,
        "analysis_name": DEFAULT_ANALYSIS_NAME,
        "analysis_description": f"Auto-processing from {message_data.get('intakeSource', 'unknown')} intake - {message_data.get('subject', 'No subject')}",
        "data_model_name": DATA_MODEL_NAME,
        "classifier_name": None,
        "language": language,
        "created_by": "triage_consumer",
        "auto_extract": True,
        # Include original message reference
        "_metadata": {
            "email_id": message_data.get("emailId"),
            "intake_source": message_data.get("intakeSource"),
            "processed_at": message_data.get("processedAt"),
        }
    }
    
    return request


def call_api(request_data: dict) -> bool:
    """
    Call the document processing API.
    
    Args:
        request_data: API request payload
        
    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info(f"📤 Calling API: {API_ENDPOINT}")
        logger.info(f"📦 Request payload:\n{json.dumps(request_data, indent=2)}")
        
        # Make API call
        response = requests.post(
            API_ENDPOINT,
            json=request_data,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        response.raise_for_status()
        
        logger.info(f"✅ API call successful: {response.status_code}")
        logger.info(f"📥 Response:\n{json.dumps(response.json(), indent=2)}")
        
        return True
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ API call failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response body: {e.response.text}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error calling API: {e}", exc_info=True)
        return False


def process_message(message_body: str) -> bool:
    """
    Process a single message from the queue.
    
    Args:
        message_body: JSON message body
        
    Returns:
        True if processing successful, False otherwise
    """
    try:
        # Parse message
        message_data = json.loads(message_body)
        
        # Print details
        print_message_details(message_data)
        
        # Build API request
        api_request = build_api_request(message_data)
        
        # Call API
        success = call_api(api_request)
        
        if success:
            logger.info("✅ Message processed successfully")
        else:
            logger.warning("⚠️  Message processed but API call failed")
        
        return success
        
    except json.JSONDecodeError as e:
        logger.error(f"❌ Failed to parse message JSON: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Error processing message: {e}", exc_info=True)
        return False


def run_consumer_loop():
    """
    Main consumer loop - continuously listens to the queue.
    """
    logger.info("="*80)
    logger.info("🚀 TRIAGE CONSUMER CLIENT STARTING")
    logger.info("="*80)
    logger.info(f"📡 Service Bus Namespace: {namespace}")
    logger.info(f"📬 Listening to queue: {TRIAGE_QUEUE}")
    logger.info(f"🔗 API Endpoint: {API_ENDPOINT}")
    logger.info(f"⏳ Waiting for messages... (Press Ctrl+C to stop)")
    logger.info("="*80 + "\n")
    
    # Create Service Bus client
    credential = DefaultAzureCredential()
    servicebus_client = ServiceBusClient(
        fully_qualified_namespace=FULLY_QUALIFIED_NAMESPACE,
        credential=credential
    )
    
    try:
        with servicebus_client:
            # Get queue receiver
            receiver = servicebus_client.get_queue_receiver(
                queue_name=TRIAGE_QUEUE,
                max_wait_time=30  # Wait up to 30 seconds for messages
            )
            
            with receiver:
                logger.info("✅ Connected to queue. Waiting for messages...\n")
                
                # Continuous loop
                while True:
                    try:
                        # Receive messages (blocking with timeout)
                        received_msgs = receiver.receive_messages(
                            max_message_count=1,
                            max_wait_time=30
                        )
                        
                        # Process each message
                        for msg in received_msgs:
                            message_body = str(msg)
                            
                            # Process the message
                            success = process_message(message_body)
                            
                            # Complete (acknowledge) the message
                            receiver.complete_message(msg)
                            
                            if success:
                                logger.info("✅ Message acknowledged and removed from queue\n")
                            else:
                                logger.warning("⚠️  Message acknowledged despite processing errors\n")
                        
                        # If no messages, log and continue waiting
                        if not received_msgs:
                            logger.debug("No messages received, waiting...")
                            
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        logger.error(f"❌ Error receiving/processing message: {e}", exc_info=True)
                        logger.info("Continuing to listen for messages...\n")
                        
    except KeyboardInterrupt:
        logger.info("\n\n🛑 Consumer stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error in consumer loop: {e}", exc_info=True)
    finally:
        logger.info("🔌 Closing Service Bus connection...")
        logger.info("👋 Goodbye!")


if __name__ == "__main__":
    run_consumer_loop()
