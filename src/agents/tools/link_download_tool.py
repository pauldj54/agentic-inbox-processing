"""
Link Download Tool — Detects download links in email bodies,
downloads documents via HTTPS, and uploads to Azure Blob Storage.

Module: src/agents/tools/link_download_tool.py
Feature: 001-download-link-intake (US1)
"""

import logging
import os
import re
import time
import uuid
import asyncio
import mimetypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse, unquote

import aiohttp
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import ContentSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes (per contracts/contracts.md §3)
# ---------------------------------------------------------------------------

@dataclass
class DownloadedFile:
    """A successfully downloaded and uploaded file."""
    path: str            # Blob path: "{emailId}/{filename}"
    source: str          # Always "link"
    url: str             # Original source URL
    content_type: str    # MIME type from download response


@dataclass
class DownloadFailure:
    """A failed download attempt."""
    url: str             # URL that failed
    error: str           # Error description
    attempted_at: str    # ISO 8601 timestamp
    error_type: str = "unknown"  # Categorized: http_error, timeout, content_type_rejected, size_exceeded, network_error
    http_status: int | None = None  # HTTP status code (if applicable)


@dataclass
class LinkDownloadResult:
    """Result of processing all download links in an email body."""
    downloaded_files: list[DownloadedFile] = field(default_factory=list)
    failures: list[DownloadFailure] = field(default_factory=list)
    urls_detected: int = 0    # Total URLs found in body
    urls_attempted: int = 0   # URLs that matched document patterns


# ---------------------------------------------------------------------------
# URL / HTML parsing helpers
# ---------------------------------------------------------------------------

# Document extension filter (research.md §1)
DOCUMENT_EXTENSION_RE = re.compile(
    r"\.(pdf|docx?|xlsx?|csv|pptx?|txt|zip)(\?.*)?$",
    re.IGNORECASE,
)

# Plain-text URL regex (research.md §1)
PLAIN_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")

# Non-document domains to skip (research.md §1)
SKIP_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "linkedin.com",
    "instagram.com", "youtube.com", "t.co", "bit.ly",
}

# MIME types considered non-document (US2: content-type rejection)
_NON_DOCUMENT_CONTENT_TYPES = {
    "text/html", "text/css", "text/javascript",
    "application/javascript", "application/xhtml+xml",
}
_NON_DOCUMENT_MIME_PREFIXES = ("image/", "video/", "audio/")


class _HrefExtractor(HTMLParser):
    """Extracts href values from <a> tags."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for attr_name, attr_value in attrs:
                if attr_name == "href" and attr_value and attr_value.startswith("http"):
                    self.urls.append(attr_value)


def _extract_urls(email_body: str) -> list[str]:
    """Extract URLs from email body (HTML or plain text)."""
    urls: list[str] = []

    # Try HTML extraction first
    if "<a " in email_body.lower() or "<a>" in email_body.lower():
        parser = _HrefExtractor()
        try:
            parser.feed(email_body)
            urls.extend(parser.urls)
        except Exception:
            pass  # Fall through to plain-text extraction

    # Also extract plain-text URLs (may find URLs not wrapped in <a> tags)
    plain_urls = PLAIN_URL_RE.findall(email_body)
    for url in plain_urls:
        # Strip trailing punctuation that may be captured
        url = url.rstrip(".,;:!?)")
        if url not in urls:
            urls.append(url)

    return urls


def _is_document_url(url: str) -> bool:
    """Check whether a URL's path matches a known document extension."""
    parsed = urlparse(url)
    domain = parsed.hostname or ""

    # Skip non-document domains
    for skip in SKIP_DOMAINS:
        if domain.endswith(skip):
            return False

    return bool(DOCUMENT_EXTENSION_RE.search(parsed.path))


def _derive_filename(url: str, response: aiohttp.ClientResponse) -> str:
    """Derive filename using priority: Content-Disposition → URL path → fallback.

    Per research.md §1 filename derivation priority.
    """
    # 1. Content-Disposition header
    if response.content_disposition and response.content_disposition.filename:
        return response.content_disposition.filename

    # 2. Last segment of URL path
    parsed = urlparse(url)
    path_segment = unquote(parsed.path.split("/")[-1])
    if path_segment and "." in path_segment:
        return path_segment

    # 3. Generated fallback
    ext = mimetypes.guess_extension(response.content_type or "") or ""
    short_id = uuid.uuid4().hex[:8]
    return f"download_{short_id}{ext}"


# ---------------------------------------------------------------------------
# Main tool class
# ---------------------------------------------------------------------------

class LinkDownloadTool:
    """Detects download links in email bodies and downloads documents to Blob Storage."""

    def __init__(
        self,
        storage_account_url: str | None = None,
        container_name: str = "attachments",
        max_file_size_bytes: int | None = None,
        download_timeout_seconds: int | None = None,
    ) -> None:
        self.storage_account_url = (
            storage_account_url
            or os.environ.get("STORAGE_ACCOUNT_URL")
        )
        if not self.storage_account_url:
            raise ValueError(
                "Storage account URL is required. "
                "Set STORAGE_ACCOUNT_URL environment variable."
            )

        self.container_name = container_name

        max_mb = int(os.environ.get("LINK_DOWNLOAD_MAX_SIZE_MB", "50"))
        self.max_file_size_bytes = max_file_size_bytes or (max_mb * 1024 * 1024)

        timeout_s = int(os.environ.get("LINK_DOWNLOAD_TIMEOUT_S", "30"))
        self.download_timeout_seconds = download_timeout_seconds or timeout_s

    async def process_email_links(
        self,
        email_id: str,
        email_body: str,
    ) -> LinkDownloadResult:
        """Scan email body for document download links, download, and upload to blob.

        Args:
            email_id: Graph API message ID (used as blob path prefix).
            email_body: Raw email body text (HTML or plain text).

        Returns:
            LinkDownloadResult with downloaded files and failures.
        """
        result = LinkDownloadResult()

        # Step 1: Extract all URLs from body
        all_urls = _extract_urls(email_body)
        result.urls_detected = len(all_urls)

        # Step 2: Filter to document URLs
        doc_urls = [u for u in all_urls if _is_document_url(u)]
        result.urls_attempted = len(doc_urls)

        if not doc_urls:
            logger.info(f"[{email_id}] No document URLs found in email body "
                        f"({result.urls_detected} total URLs detected)")
            return result

        logger.info(f"[{email_id}] Found {len(doc_urls)} document URL(s) to download")

        # Step 3: Download each document and upload to blob
        credential = DefaultAzureCredential()
        try:
            blob_service = BlobServiceClient(
                account_url=self.storage_account_url,
                credential=credential,
            )
            timeout = aiohttp.ClientTimeout(total=self.download_timeout_seconds)

            async with blob_service, aiohttp.ClientSession(timeout=timeout) as session:
                for url in doc_urls:
                    downloaded = await self._download_and_upload(
                        session, blob_service, email_id, url, result,
                    )
                    if downloaded:
                        result.downloaded_files.append(downloaded)
        finally:
            await credential.close()

        logger.info(
            f"[{email_id}] Link download complete: "
            f"{len(result.downloaded_files)} downloaded, "
            f"{len(result.failures)} failed"
        )
        return result

    async def _download_and_upload(
        self,
        session: aiohttp.ClientSession,
        blob_service: BlobServiceClient,
        email_id: str,
        url: str,
        result: LinkDownloadResult,
    ) -> DownloadedFile | None:
        """Download a single file and upload to blob storage.

        Returns DownloadedFile on success, None on failure (failure appended to result).
        """
        start = time.monotonic()
        log_ctx = {"email_id": email_id, "url": url}
        logger.info("Download attempt starting", extra=log_ctx)

        try:
            async with session.get(url) as response:
                elapsed = time.monotonic() - start
                log_ctx["http_status"] = response.status
                log_ctx["elapsed_s"] = round(elapsed, 3)

                if response.status != 200:
                    failure = DownloadFailure(
                        url=url,
                        error=f"HTTP {response.status}",
                        attempted_at=datetime.now(timezone.utc).isoformat(),
                        error_type="http_error",
                        http_status=response.status,
                    )
                    result.failures.append(failure)
                    logger.warning("Download failed: HTTP error", extra={**log_ctx, "error_type": "http_error"})
                    return None

                # Content-type rejection (US2: non-document MIME types)
                content_type = response.content_type or "application/octet-stream"
                ct_lower = content_type.lower().split(";")[0].strip()
                if ct_lower in _NON_DOCUMENT_CONTENT_TYPES or any(
                    ct_lower.startswith(p) for p in _NON_DOCUMENT_MIME_PREFIXES
                ):
                    failure = DownloadFailure(
                        url=url,
                        error=f"Non-document content-type: {content_type}",
                        attempted_at=datetime.now(timezone.utc).isoformat(),
                        error_type="content_type_rejected",
                    )
                    result.failures.append(failure)
                    logger.warning(
                        "Download rejected: non-document content-type",
                        extra={**log_ctx, "error_type": "content_type_rejected", "content_type": content_type},
                    )
                    return None

                # Check Content-Length if available
                content_length = response.content_length
                if content_length and content_length > self.max_file_size_bytes:
                    failure = DownloadFailure(
                        url=url,
                        error=f"File size {content_length} exceeds limit {self.max_file_size_bytes}",
                        attempted_at=datetime.now(timezone.utc).isoformat(),
                        error_type="size_exceeded",
                    )
                    result.failures.append(failure)
                    logger.warning(
                        "Download rejected: size exceeds limit",
                        extra={**log_ctx, "error_type": "size_exceeded", "content_length": content_length},
                    )
                    return None

                # Derive filename
                filename = _derive_filename(url, response)
                blob_path = f"{email_id}/{filename}"

                # Stream download with size enforcement
                chunks: list[bytes] = []
                total_bytes = 0
                async for chunk in response.content.iter_chunked(8192):
                    total_bytes += len(chunk)
                    if total_bytes > self.max_file_size_bytes:
                        failure = DownloadFailure(
                            url=url,
                            error=f"Download exceeded size limit ({self.max_file_size_bytes} bytes) during streaming",
                            attempted_at=datetime.now(timezone.utc).isoformat(),
                            error_type="size_exceeded",
                        )
                        result.failures.append(failure)
                        logger.warning(
                            "Download aborted: streaming size limit exceeded",
                            extra={**log_ctx, "error_type": "size_exceeded", "bytes_received": total_bytes},
                        )
                        return None
                    chunks.append(chunk)

                file_data = b"".join(chunks)

                # Upload to blob storage
                container_client = blob_service.get_container_client(self.container_name)
                blob_client = container_client.get_blob_client(blob_path)
                await blob_client.upload_blob(
                    file_data,
                    overwrite=True,
                    content_settings=ContentSettings(content_type=content_type),
                )

                elapsed = time.monotonic() - start
                logger.info(
                    "Download+upload succeeded",
                    extra={
                        **log_ctx,
                        "blob_path": blob_path,
                        "bytes": total_bytes,
                        "content_type": content_type,
                        "elapsed_s": round(elapsed, 3),
                    },
                )

                return DownloadedFile(
                    path=blob_path,
                    source="link",
                    url=url,
                    content_type=content_type,
                )

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            failure = DownloadFailure(
                url=url,
                error=f"Download timed out after {self.download_timeout_seconds}s",
                attempted_at=datetime.now(timezone.utc).isoformat(),
                error_type="timeout",
            )
            result.failures.append(failure)
            logger.warning(
                "Download failed: timeout",
                extra={**log_ctx, "error_type": "timeout", "elapsed_s": round(elapsed, 3)},
            )
            return None

        except aiohttp.ClientError as exc:
            elapsed = time.monotonic() - start
            failure = DownloadFailure(
                url=url,
                error=str(exc),
                attempted_at=datetime.now(timezone.utc).isoformat(),
                error_type="network_error",
            )
            result.failures.append(failure)
            logger.warning(
                "Download failed: network error",
                extra={**log_ctx, "error_type": "network_error", "error": str(exc), "elapsed_s": round(elapsed, 3)},
            )
            return None

        except Exception as exc:
            elapsed = time.monotonic() - start
            failure = DownloadFailure(
                url=url,
                error=str(exc),
                attempted_at=datetime.now(timezone.utc).isoformat(),
                error_type="unknown",
            )
            result.failures.append(failure)
            logger.error(
                "Download failed: unexpected error",
                extra={**log_ctx, "error_type": "unknown", "error": str(exc), "elapsed_s": round(elapsed, 3)},
                exc_info=True,
            )
            return None
