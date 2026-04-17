"""
Link Download Tool — Detects download links in email bodies,
downloads documents via HTTPS, and uploads to Azure Blob Storage.

Module: src/agents/tools/link_download_tool.py
Feature: 001-download-link-intake (US1)
"""

import base64
import hashlib
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

import urllib.request
import urllib.error
import http.client
import socket
import ssl

import aiohttp
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import ContentSettings

from .allowed_content_types import (
    is_allowed_content_type,
    is_allowed_extension,
    ALLOWED_CONTENT_TYPES,
    ALLOWED_EXTENSIONS,
)

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
    content_md5: str | None = None  # Base64-encoded MD5 of file content


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

# Document extension filter — broad set of document-like extensions.
# URLs matching these extensions are candidates for download; the actual
# security enforcement (PDF-only policy) happens AFTER the HTTP response
# is received via the content-type gate.  This pre-filter must be BROAD
# so that non-allowed document types are still attempted, rejected, and
# logged as failures (rather than silently ignored).
_DOCUMENT_EXTENSIONS = {
    "pdf", "csv", "xlsx", "xls", "docx", "doc", "pptx", "ppt",
    "txt", "rtf", "odt", "ods", "odp", "tsv", "json", "xml",
}
DOCUMENT_EXTENSION_RE = re.compile(
    r"\.(" + "|".join(re.escape(ext) for ext in sorted(_DOCUMENT_EXTENSIONS)) + r")(\?.*)?$",
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
        cosmos_tools=None,
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
        self.cosmos_tools = cosmos_tools

        max_mb = int(os.environ.get("LINK_DOWNLOAD_MAX_SIZE_MB", "50"))
        self.max_file_size_bytes = max_file_size_bytes or (max_mb * 1024 * 1024)

        timeout_s = int(os.environ.get("LINK_DOWNLOAD_TIMEOUT_S", "30"))
        self.download_timeout_seconds = download_timeout_seconds or timeout_s

    async def process_email_links(
        self,
        email_id: str,
        email_body: str,
        partition_key: str | None = None,
    ) -> LinkDownloadResult:
        """Scan email body for document download links, download, and upload to blob.

        Args:
            email_id: Graph API message ID (used as blob path prefix).
            email_body: Raw email body text (HTML or plain text).
            partition_key: Cosmos DB partition key for delivery tracking dedup.

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

        # Delivery tracking dedup (T014/T015): check each downloaded file
        # against existing records in the same partition.
        if self.cosmos_tools and partition_key:
            for downloaded in result.downloaded_files:
                if not downloaded.content_md5:
                    continue
                try:
                    existing = self.cosmos_tools.find_by_content_hash(
                        downloaded.content_md5, partition_key
                    )
                    if existing:
                        self.cosmos_tools.increment_delivery_count(
                            existing, downloaded.content_md5, action="duplicate"
                        )
                        logger.info(
                            "Link download dedup: duplicate detected",
                            extra={
                                "email_id": email_id,
                                "content_hash": downloaded.content_md5,
                                "matched_record_id": existing.get("id", "")[:20],
                                "action": "duplicate",
                            },
                        )
                    else:
                        # T020: Filename-match content update detection
                        filename = downloaded.path.split("/")[-1] if "/" in downloaded.path else downloaded.path
                        filename_match = self.cosmos_tools.find_by_filename(
                            filename, partition_key
                        )
                        if filename_match and filename_match.get("contentHash") != downloaded.content_md5:
                            self.cosmos_tools.increment_delivery_count(
                                filename_match, downloaded.content_md5, action="update"
                            )
                            logger.info(
                                "Link download dedup: content update detected",
                                extra={
                                    "email_id": email_id,
                                    "content_hash": downloaded.content_md5,
                                    "matched_record_id": filename_match.get("id", "")[:20],
                                    "action": "update",
                                },
                            )
                        else:
                            logger.info(
                                "Link download dedup: new content",
                                extra={
                                    "email_id": email_id,
                                    "content_hash": downloaded.content_md5,
                                    "action": "new",
                                },
                            )
                except Exception as exc:
                    logger.warning(
                        f"Link download dedup check failed: {exc}",
                        extra={"email_id": email_id, "url": downloaded.url},
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

                # Allowed content-type enforcement (security policy)
                # Derive filename early so we can check extension as fallback
                filename_check = _derive_filename(url, response)
                if not is_allowed_content_type(content_type) and not is_allowed_extension(filename_check):
                    allowed_str = ", ".join(sorted(ALLOWED_CONTENT_TYPES))
                    failure = DownloadFailure(
                        url=url,
                        error=f"Content type '{content_type}' not in allowed list ({allowed_str})",
                        attempted_at=datetime.now(timezone.utc).isoformat(),
                        error_type="content_type_not_allowed",
                    )
                    result.failures.append(failure)
                    logger.warning(
                        "Download rejected: content type not in allowed list",
                        extra={**log_ctx, "error_type": "content_type_not_allowed", "content_type": content_type},
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

                # Compute content MD5 for delivery tracking
                content_md5 = base64.b64encode(
                    hashlib.md5(file_data).digest()
                ).decode()

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
                    content_md5=content_md5,
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
            # IDNA encoding errors may surface as ClientConnectorError.
            # Fall back to stdlib urllib which does not require the idna package.
            if "idna" in str(exc).lower() or "unicode" in str(exc).lower():
                logger.warning(
                    "aiohttp IDNA/unicode error, retrying with urllib fallback",
                    extra={**log_ctx, "original_error": str(exc)},
                )
                return await self._download_with_urllib_fallback(
                    blob_service, email_id, url, result,
                )

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
            # IDNA encoding errors surface as UnicodeError inside aiohttp/yarl.
            # Fall back to stdlib urllib which does not require the idna package.
            if "idna" in str(exc).lower() or "UnicodeError" in type(exc).__name__:
                logger.warning(
                    "aiohttp IDNA error, retrying with urllib fallback",
                    extra={**log_ctx, "original_error": str(exc)},
                )
                return await self._download_with_urllib_fallback(
                    blob_service, email_id, url, result,
                )

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

    async def _download_with_urllib_fallback(
        self,
        blob_service: BlobServiceClient,
        email_id: str,
        url: str,
        result: LinkDownloadResult,
    ) -> DownloadedFile | None:
        """Fallback download that bypasses Python's IDNA codec.

        On Azure App Service Linux + Python 3.12, the built-in encodings.idna
        codec (used by socket.getaddrinfo for str hostnames) fails with
        'label empty or too long' for certain hostnames. This fallback:
        1. Resolves the hostname to an IP by passing hostname as bytes to
           socket.getaddrinfo (bytes bypass the IDNA codec at the C level).
        2. Creates a raw TCP socket to the resolved IP.
        3. Wraps the socket in SSL with proper SNI (server_hostname).
        4. Sends the HTTP request with the correct Host header.
        """
        start = time.monotonic()
        log_ctx = {"email_id": email_id, "url": url, "method": "direct_https_fallback"}

        try:
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
            port = parsed.port or 443
            request_path = parsed.path or "/"
            if parsed.query:
                request_path += "?" + parsed.query

            loop = asyncio.get_event_loop()

            def _sync_download():
                # Resolve hostname with bytes to bypass IDNA codec
                addr_info = socket.getaddrinfo(
                    hostname.encode("ascii"), port,
                    socket.AF_INET, socket.SOCK_STREAM,
                )
                ip = addr_info[0][4][0]
                logger.info(
                    f"DNS resolved (IDNA bypass): {hostname} -> {ip}",
                    extra=log_ctx,
                )

                # TCP connect to IP
                sock = socket.create_connection(
                    (ip, port), timeout=self.download_timeout_seconds
                )
                # SSL with SNI
                ctx = ssl.create_default_context()
                ssl_sock = ctx.wrap_socket(sock, server_hostname=hostname)

                # HTTP request
                conn = http.client.HTTPSConnection(hostname, port)
                conn.sock = ssl_sock
                conn.request(
                    "GET", request_path,
                    headers={"Host": hostname, "User-Agent": "agentic-inbox/1.0"},
                )
                return conn.getresponse()

            response = await loop.run_in_executor(None, _sync_download)
            status = response.status

            if status != 200:
                failure = DownloadFailure(
                    url=url,
                    error=f"HTTP {status}",
                    attempted_at=datetime.now(timezone.utc).isoformat(),
                    error_type="http_error",
                    http_status=status,
                )
                result.failures.append(failure)
                return None

            content_type = response.getheader("Content-Type", "application/octet-stream")
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
                return None

            # Derive filename from URL path
            path_segment = unquote(parsed.path.split("/")[-1])
            if path_segment and "." in path_segment:
                filename = path_segment
            else:
                ext = mimetypes.guess_extension(content_type) or ""
                filename = f"download_{uuid.uuid4().hex[:8]}{ext}"

            # Check allowed content type / extension
            if not is_allowed_content_type(content_type) and not is_allowed_extension(filename):
                allowed_str = ", ".join(sorted(ALLOWED_CONTENT_TYPES))
                failure = DownloadFailure(
                    url=url,
                    error=f"Content type '{content_type}' not in allowed list ({allowed_str})",
                    attempted_at=datetime.now(timezone.utc).isoformat(),
                    error_type="content_type_not_allowed",
                )
                result.failures.append(failure)
                return None

            file_data = await loop.run_in_executor(None, response.read)

            if len(file_data) > self.max_file_size_bytes:
                failure = DownloadFailure(
                    url=url,
                    error=f"File size {len(file_data)} exceeds limit {self.max_file_size_bytes}",
                    attempted_at=datetime.now(timezone.utc).isoformat(),
                    error_type="size_exceeded",
                )
                result.failures.append(failure)
                return None

            content_md5 = base64.b64encode(
                hashlib.md5(file_data).digest()
            ).decode()

            blob_path = f"{email_id}/{filename}"
            container_client = blob_service.get_container_client(self.container_name)
            blob_client = container_client.get_blob_client(blob_path)
            await blob_client.upload_blob(
                file_data,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )

            elapsed = time.monotonic() - start
            logger.info(
                "Direct HTTPS fallback download+upload succeeded",
                extra={
                    **log_ctx,
                    "blob_path": blob_path,
                    "bytes": len(file_data),
                    "content_type": content_type,
                    "elapsed_s": round(elapsed, 3),
                },
            )

            return DownloadedFile(
                path=blob_path,
                source="link",
                url=url,
                content_type=content_type,
                content_md5=content_md5,
            )

        except Exception as exc:
            elapsed = time.monotonic() - start
            failure = DownloadFailure(
                url=url,
                error=f"direct HTTPS fallback failed: {exc}",
                attempted_at=datetime.now(timezone.utc).isoformat(),
                error_type="unknown",
            )
            result.failures.append(failure)
            logger.error(
                "Direct HTTPS fallback download failed",
                extra={**log_ctx, "error": str(exc), "elapsed_s": round(elapsed, 3)},
                exc_info=True,
            )
            return None
