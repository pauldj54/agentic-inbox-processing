"""
FastAPI Web Application for Email Processing Dashboard
Displays emails from Cosmos DB and messages from Service Bus queues.
"""

import os
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
import asyncio
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from azure.identity import DefaultAzureCredential  # Use DefaultAzureCredential (more robust)
from azure.cosmos import CosmosClient  # Use SYNC Cosmos client
from azure.servicebus import ServiceBusClient  # Use SYNC Service Bus client

# Load environment variables from .env01 in project root
# Try multiple paths to handle different run contexts
env_paths = [
    Path(__file__).parent.parent.parent / ".env01",  # From src/webapp/main.py
    Path.cwd() / ".env01",  # From current working directory
]

for env_path in env_paths:
    if env_path.exists():
        load_dotenv(env_path)
        print(f"Loaded environment from: {env_path}")
        break

# Configuration
COSMOS_ENDPOINT = os.environ["COSMOS_ENDPOINT"]
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "email-processing")
SERVICEBUS_NAMESPACE = os.environ["SERVICEBUS_NAMESPACE"]

# Queue names (simplified pipeline)
QUEUE_NAMES = [
    "email-intake",      # Incoming emails
    "discarded",         # Non-PE emails
    "human-review",      # Low confidence (<65%)
    "archival-pending",  # Ready for archival (>=65%)
]

# Global clients (initialized on startup)
credential = None
cosmos_client = None
servicebus_client = None
executor = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup sync clients."""
    global credential, cosmos_client, servicebus_client, executor
    
    # Use DefaultAzureCredential (tries multiple auth methods)
    credential = DefaultAzureCredential()
    cosmos_client = CosmosClient(COSMOS_ENDPOINT, credential=credential)
    servicebus_client = ServiceBusClient(
        fully_qualified_namespace=f"{SERVICEBUS_NAMESPACE}.servicebus.windows.net",
        credential=credential
    )
    executor = ThreadPoolExecutor(max_workers=2)  # Reduce workers to avoid auth conflicts
    
    yield
    
    # Cleanup
    servicebus_client.close()
    executor.shutdown(wait=False)


app = FastAPI(
    title="Email Processing Dashboard",
    description="Monitor email processing status and queue messages",
    lifespan=lifespan
)

# Templates
templates_path = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_path))


import re
from html.parser import HTMLParser


class HTMLTextExtractor(HTMLParser):
    """Extract plain text from HTML."""
    def __init__(self):
        super().__init__()
        self.text_parts = []
        
    def handle_data(self, data):
        self.text_parts.append(data)
        
    def get_text(self) -> str:
        return ' '.join(self.text_parts)


def strip_html(html_content: str) -> str:
    """Remove HTML tags, comments, and return plain text."""
    if not html_content:
        return ""
    
    # Quick check if content looks like HTML
    if '<' not in html_content:
        return html_content
    
    try:
        # First, remove HTML comments <!-- ... -->
        text = re.sub(r'<!--.*?-->', ' ', html_content, flags=re.DOTALL)
        
        # Remove style tags and their content
        text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove script tags and their content
        text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Now use the HTML parser for remaining tags
        parser = HTMLTextExtractor()
        parser.feed(text)
        text = parser.get_text()
        
        # Clean up extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except:
        # Fallback: simple regex to strip all tags and comments
        text = re.sub(r'<!--.*?-->', ' ', html_content, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text


def truncate_text(text: Optional[str], max_length: int = 50) -> str:
    """Truncate text to max_length with ellipsis."""
    if not text:
        return ""
    text = str(text).replace("\n", " ").replace("\r", "")
    if len(text) > max_length:
        return text[:max_length] + "..."
    return text


def truncate_html(html_content: Optional[str], max_length: int = 50) -> str:
    """Strip HTML and truncate to max_length with ellipsis."""
    if not html_content:
        return ""
    plain_text = strip_html(str(html_content))
    return truncate_text(plain_text, max_length)


def format_datetime(dt_value) -> str:
    """Format datetime for display."""
    if not dt_value:
        return "N/A"
    if isinstance(dt_value, str):
        try:
            dt_value = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
        except:
            return dt_value
    if isinstance(dt_value, datetime):
        return dt_value.strftime("%Y-%m-%d %H:%M:%S")
    return str(dt_value)


def parse_bool(value) -> bool:
    """Parse a value that might be a string boolean to actual boolean.
    Handles: 'True', 'true', 'FALSE', True, False, None, etc.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1")
    return bool(value)


# Add template filters
templates.env.filters["truncate_text"] = truncate_text
templates.env.filters["truncate_html"] = truncate_html
templates.env.filters["format_datetime"] = format_datetime
templates.env.filters["parse_bool"] = parse_bool


def normalize_attachments(attachment_paths) -> list[dict]:
    """Normalize attachmentPaths to a list of {path, source} dicts.

    Handles:
      - object entries: {"path": "...", "source": "link"|"attachment"}
      - legacy string entries: "some/path.pdf" → {"path": "...", "source": "attachment"}
      - None / missing → []
    """
    if not attachment_paths:
        return []
    result: list[dict] = []
    for entry in attachment_paths:
        if isinstance(entry, dict):
            result.append({
                "path": entry.get("path", ""),
                "source": entry.get("source", "attachment"),
            })
        elif isinstance(entry, str):
            result.append({"path": entry, "source": "attachment"})
    return result


def attachment_source_icon(source: str) -> str:
    """Return a display icon + label for an attachment source."""
    if source == "link":
        return "🔗 link"
    return "📎 attachment"


templates.env.filters["normalize_attachments"] = normalize_attachments
templates.env.filters["attachment_source_icon"] = attachment_source_icon


def _get_emails_from_cosmos_sync(limit: int = 20) -> list:
    """Fetch recent emails from Cosmos DB (sync)."""
    try:
        database = cosmos_client.get_database_client(COSMOS_DATABASE)
        container = database.get_container_client("emails")
        
        query = """
            SELECT TOP @limit *
            FROM c
            ORDER BY c.receivedAt DESC
        """
        
        emails = []
        for item in container.query_items(
            query=query,
            parameters=[{"name": "@limit", "value": limit}],
            enable_cross_partition_query=True
        ):
            emails.append(item)
        
        return emails
    except Exception as e:
        print(f"Error fetching emails from Cosmos DB: {e}")
        return []


async def get_emails_from_cosmos(limit: int = 20) -> list:
    """Fetch recent emails from Cosmos DB."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, _get_emails_from_cosmos_sync, limit)


def _get_queue_messages_sync(queue_name: str, max_count: int = 10) -> list:
    """Peek messages from a Service Bus queue (sync)."""
    import json
    import re
    
    try:
        print(f"Peeking queue: {queue_name}")
        receiver = servicebus_client.get_queue_receiver(queue_name=queue_name)
        with receiver:
            messages = receiver.peek_messages(max_message_count=max_count)
            print(f"Found {len(messages)} messages in {queue_name}")
            
            result = []
            for msg in messages:
                body = None
                body_str = str(msg)
                
                # The Logic App sends JSON with raw control characters in string values.
                # We need to escape them before parsing.
                def fix_json_control_chars(s):
                    """Fix JSON by escaping control characters inside string values."""
                    # This regex-based approach escapes control chars while preserving JSON structure
                    # Replace literal control characters (not already escaped) with escaped versions
                    result_chars = []
                    in_string = False
                    escape_next = False
                    
                    for char in s:
                        if escape_next:
                            result_chars.append(char)
                            escape_next = False
                        elif char == '\\':
                            result_chars.append(char)
                            escape_next = True
                        elif char == '"':
                            result_chars.append(char)
                            in_string = not in_string
                        elif in_string and ord(char) < 32:
                            # Escape control characters inside strings
                            if char == '\n':
                                result_chars.append('\\n')
                            elif char == '\r':
                                result_chars.append('\\r')
                            elif char == '\t':
                                result_chars.append('\\t')
                            else:
                                result_chars.append(f'\\u{ord(char):04x}')
                        else:
                            result_chars.append(char)
                    
                    return ''.join(result_chars)
                
                # Try to parse as JSON with fixed control chars
                try:
                    fixed_json = fix_json_control_chars(body_str)
                    body = json.loads(fixed_json)
                except json.JSONDecodeError as e:
                    print(f"JSON parse error (after fix): {e}")
                    print(f"Body preview (repr): {repr(body_str[:400])}")
                    
                    # Fallback: extract key fields using regex
                    email_match = re.search(r'"emailId":\s*"([^"]*)"', body_str)
                    from_match = re.search(r'"from":\s*"([^"]*)"', body_str)
                    subject_match = re.search(r'"subject":\s*"([^"]*)"', body_str)
                    
                    body = {
                        "emailId": email_match.group(1) if email_match else "unknown",
                        "from": from_match.group(1) if from_match else "unknown",
                        "subject": subject_match.group(1) if subject_match else "unknown",
                        "parse_note": "Extracted via regex due to parsing issues"
                    }
                
                result.append({
                    "sequence_number": msg.sequence_number,
                    "enqueued_time": msg.enqueued_time_utc,
                    "body": body
                })
            
            return result
    except Exception as e:
        import traceback
        print(f"Error peeking queue {queue_name}: {e}")
        traceback.print_exc()
        return []


async def get_queue_messages(queue_name: str, max_count: int = 10) -> list:
    """Peek messages from a Service Bus queue."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, _get_queue_messages_sync, queue_name, max_count)


def _get_pe_event_stats_sync() -> dict:
    """Get PE event statistics (sync)."""
    try:
        database = cosmos_client.get_database_client(COSMOS_DATABASE)
        container = database.get_container_client("pe-events")
        
        # Count events by type
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
        
        stats["duplicateEmails"] = stats["totalEmails"] - stats["totalEvents"]
        return stats
        
    except Exception as e:
        print(f"Error getting PE event stats: {e}")
        return None


async def get_pe_event_stats() -> dict:
    """Get PE event statistics."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, _get_pe_event_stats_sync)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    
    # Fetch emails from Cosmos DB
    emails = await get_emails_from_cosmos(limit=20)
    
    # Fetch PE event stats
    pe_stats = await get_pe_event_stats()
    
    # Fetch messages from all queues (sequentially to avoid auth conflicts)
    queue_messages = {}
    for queue_name in QUEUE_NAMES:
        try:
            queue_messages[queue_name] = await get_queue_messages(queue_name, max_count=10)
        except Exception as e:
            print(f"Failed to get messages from {queue_name}: {e}")
            queue_messages[queue_name] = []
    
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "emails": emails,
            "pe_stats": pe_stats,
            "queue_messages": queue_messages,
            "queue_names": QUEUE_NAMES
        }
    )


@app.get("/health")
async def health_check():
    """Health check endpoint for Azure App Service."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/classifications")
async def get_classifications(limit: int = 20):
    """Get recent classification results from Cosmos DB."""
    try:
        database = cosmos_client.get_database_client(COSMOS_DATABASE)
        container = database.get_container_client("classifications")
        
        query = """
            SELECT TOP @limit *
            FROM c
            ORDER BY c._ts DESC
        """
        
        results = []
        async for item in container.query_items(
            query=query,
            parameters=[{"name": "@limit", "value": limit}]
        ):
            results.append(item)
        
        return {"classifications": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e), "classifications": []}


@app.get("/api/audit-logs")
async def get_audit_logs(email_id: str = None, limit: int = 50):
    """Get audit logs for classification events."""
    try:
        database = cosmos_client.get_database_client(COSMOS_DATABASE)
        container = database.get_container_client("audit-logs")
        
        if email_id:
            query = """
                SELECT TOP @limit *
                FROM c
                WHERE c.email_id = @email_id
                ORDER BY c._ts DESC
            """
            params = [
                {"name": "@limit", "value": limit},
                {"name": "@email_id", "value": email_id}
            ]
        else:
            query = """
                SELECT TOP @limit *
                FROM c
                ORDER BY c._ts DESC
            """
            params = [{"name": "@limit", "value": limit}]
        
        results = []
        async for item in container.query_items(query=query, parameters=params):
            results.append(item)
        
        return {"audit_logs": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e), "audit_logs": []}


@app.get("/api/queue-counts")
async def get_queue_counts():
    """Get message counts for all queues (for real-time monitoring)."""
    counts = {}
    for queue_name in QUEUE_NAMES:
        try:
            receiver = servicebus_client.get_queue_receiver(queue_name=queue_name)
            async with receiver:
                messages = await receiver.peek_messages(max_message_count=100)
                counts[queue_name] = len(messages)
        except Exception as e:
            counts[queue_name] = f"Error: {e}"
    return counts


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
