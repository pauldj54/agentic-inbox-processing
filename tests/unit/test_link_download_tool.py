"""
Unit tests for LinkDownloadTool (US1 & US2).

Tests cover:
  US1:
  - URL regex extraction from plain text and HTML bodies
  - Document extension filtering (match .pdf, .docx; reject .html, .jpg)
  - Filename derivation from Content-Disposition / URL path / generated fallback
  - Non-document domain skipping (twitter, facebook, etc.)

  US2:
  - HTTP 404/500 → DownloadFailure with error_type="http_error"
  - Timeout → DownloadFailure with error_type="timeout"
  - HTML content-type rejection → error_type="content_type_rejected"
  - File size exceeded → error_type="size_exceeded"
  - Network error → error_type="network_error"
  - Email processing continues with partial results
"""

import asyncio
import socket

import aiohttp
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from src.agents.tools.link_download_tool import (
    _extract_urls,
    _is_document_url,
    _derive_filename,
    LinkDownloadTool,
    LinkDownloadResult,
    DownloadedFile,
    DownloadFailure,
    DOCUMENT_EXTENSION_RE,
    SKIP_DOMAINS,
)


# =====================================================================
# URL Extraction
# =====================================================================

class TestExtractUrls:
    """Tests for _extract_urls() — plain text and HTML."""

    def test_plain_text_single_url(self):
        body = "Please download from https://example.com/report.pdf thanks."
        urls = _extract_urls(body)
        assert urls == ["https://example.com/report.pdf"]

    def test_plain_text_multiple_urls(self):
        body = (
            "File 1: https://example.com/a.pdf\n"
            "File 2: https://example.com/b.docx"
        )
        urls = _extract_urls(body)
        assert "https://example.com/a.pdf" in urls
        assert "https://example.com/b.docx" in urls
        assert len(urls) == 2

    def test_html_href_extraction(self):
        body = '<p>Download <a href="https://host.com/file.xlsx">here</a>.</p>'
        urls = _extract_urls(body)
        assert "https://host.com/file.xlsx" in urls

    def test_html_and_plain_deduplication(self):
        """URLs in both href and visible text should not be duplicated."""
        body = (
            '<a href="https://example.com/doc.pdf">https://example.com/doc.pdf</a>'
        )
        urls = _extract_urls(body)
        assert urls.count("https://example.com/doc.pdf") == 1

    def test_empty_body(self):
        assert _extract_urls("") == []

    def test_no_urls(self):
        assert _extract_urls("Hello, no links here.") == []

    def test_strips_trailing_punctuation(self):
        body = "See https://example.com/report.pdf."
        urls = _extract_urls(body)
        assert urls == ["https://example.com/report.pdf"]

    def test_http_scheme(self):
        body = "http://example.com/legacy.csv"
        urls = _extract_urls(body)
        assert "http://example.com/legacy.csv" in urls

    def test_url_with_query_string(self):
        body = "Download: https://host.com/file.pdf?token=abc123"
        urls = _extract_urls(body)
        assert "https://host.com/file.pdf?token=abc123" in urls


# =====================================================================
# Document Extension Filtering
# =====================================================================

class TestIsDocumentUrl:
    """Tests for _is_document_url() — extension and domain checks."""

    @pytest.mark.parametrize("url", [
        "https://host.com/report.pdf",
        "https://host.com/report.PDF",
    ])
    def test_allowed_extensions_accepted(self, url):
        assert _is_document_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://host.com/data.xlsx",
        "https://host.com/doc.docx",
        "https://host.com/sheet.csv",
        "https://host.com/slides.pptx",
        "https://host.com/readme.txt",
        "https://host.com/old.doc",
        "https://host.com/old.xls",
        "https://host.com/old.ppt",
    ])
    def test_document_extensions_accepted(self, url):
        """Document-type extensions pass the URL pre-filter (content-type gate enforces policy)."""
        assert _is_document_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://host.com/archive.zip",
        "https://host.com/page.html",
        "https://host.com/image.jpg",
        "https://host.com/image.png",
        "https://host.com/video.mp4",
        "https://host.com/style.css",
        "https://host.com/script.js",
        "https://host.com/noextension",
    ])
    def test_non_document_extensions_rejected(self, url):
        assert _is_document_url(url) is False

    def test_query_string_after_extension(self):
        url = "https://host.com/report.pdf?token=abc"
        assert _is_document_url(url) is True

    @pytest.mark.parametrize("domain", list(SKIP_DOMAINS))
    def test_skip_domains_rejected(self, domain):
        url = f"https://{domain}/something.pdf"
        assert _is_document_url(url) is False

    def test_subdomain_of_skip_domain_rejected(self):
        url = "https://cdn.twitter.com/report.pdf"
        assert _is_document_url(url) is False

    def test_case_insensitive_extension(self):
        assert _is_document_url("https://host.com/FILE.PDF") is True
        # Document extensions pass URL pre-filter regardless of case
        assert _is_document_url("https://host.com/file.Docx") is True
        assert _is_document_url("https://host.com/file.CSV") is True


# =====================================================================
# Filename Derivation
# =====================================================================

class TestDeriveFilename:
    """Tests for _derive_filename() — priority: Content-Disposition → URL → fallback."""

    def _make_response(
        self,
        *,
        content_disposition_filename=None,
        content_type="application/pdf",
    ):
        """Create a mock aiohttp.ClientResponse."""
        resp = MagicMock()
        resp.content_type = content_type
        if content_disposition_filename:
            resp.content_disposition = MagicMock()
            resp.content_disposition.filename = content_disposition_filename
        else:
            resp.content_disposition = None
        return resp

    def test_content_disposition_takes_priority(self):
        resp = self._make_response(content_disposition_filename="quarterly_report.pdf")
        filename = _derive_filename("https://host.com/whatever", resp)
        assert filename == "quarterly_report.pdf"

    def test_url_path_when_no_disposition(self):
        resp = self._make_response()
        filename = _derive_filename("https://host.com/docs/annual_report.pdf", resp)
        assert filename == "annual_report.pdf"

    def test_url_encoded_filename(self):
        resp = self._make_response()
        filename = _derive_filename(
            "https://host.com/docs/my%20report.pdf", resp,
        )
        assert filename == "my report.pdf"

    def test_generated_fallback_when_no_extension_in_url(self):
        resp = self._make_response(content_type="application/pdf")
        filename = _derive_filename("https://host.com/download", resp)
        assert filename.startswith("download_")
        assert filename.endswith(".pdf")

    def test_generated_fallback_unknown_content_type(self):
        resp = self._make_response(content_type="application/octet-stream")
        filename = _derive_filename("https://host.com/download", resp)
        assert filename.startswith("download_")


# =====================================================================
# LinkDownloadTool — process_email_links orchestration
# =====================================================================

class TestProcessEmailLinks:
    """Tests for LinkDownloadTool.process_email_links() with mocked I/O."""

    @pytest.fixture
    def tool(self):
        """Create a LinkDownloadTool with a fake storage URL."""
        with patch.dict("os.environ", {"STORAGE_ACCOUNT_URL": "https://fake.blob.core.windows.net"}):
            return LinkDownloadTool()

    @pytest.mark.asyncio
    async def test_no_urls_returns_empty_result(self, tool):
        """Email body with no URLs should return zeros."""
        with patch(
            "src.agents.tools.link_download_tool.DefaultAzureCredential"
        ) as MockCred:
            mock_cred_instance = AsyncMock()
            MockCred.return_value = mock_cred_instance

            result = await tool.process_email_links("email123", "No links here.")

        assert isinstance(result, LinkDownloadResult)
        assert result.urls_detected == 0
        assert result.urls_attempted == 0
        assert result.downloaded_files == []
        assert result.failures == []

    @pytest.mark.asyncio
    async def test_non_document_urls_not_attempted(self, tool):
        """URLs without document extensions should be detected but not downloaded."""
        body = "Visit https://example.com/page.html for more info."

        with patch(
            "src.agents.tools.link_download_tool.DefaultAzureCredential"
        ) as MockCred:
            mock_cred_instance = AsyncMock()
            MockCred.return_value = mock_cred_instance

            result = await tool.process_email_links("email123", body)

        assert result.urls_detected == 1
        assert result.urls_attempted == 0

    @pytest.mark.asyncio
    async def test_successful_download_and_upload(self, tool):
        """A valid document URL should be downloaded and uploaded to blob."""
        body = "Download: https://host.com/report.pdf"
        pdf_bytes = b"%PDF-1.4 fake content"

        # Mock aiohttp response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.content_length = len(pdf_bytes)
        mock_response.content_type = "application/pdf"
        mock_response.content_disposition = None

        # iter_chunked returns the bytes in one chunk
        async def fake_iter_chunked(size):
            yield pdf_bytes
        mock_response.content.iter_chunked = fake_iter_chunked

        # session.get() must be an async context manager
        @asynccontextmanager
        async def fake_get(url):
            yield mock_response

        # Mock aiohttp session
        mock_session = MagicMock()
        mock_session.get = fake_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        # Mock blob client
        mock_blob_client = AsyncMock()
        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client
        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container_client
        mock_blob_service.__aenter__ = AsyncMock(return_value=mock_blob_service)
        mock_blob_service.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.agents.tools.link_download_tool.DefaultAzureCredential"
        ) as MockCred, patch(
            "src.agents.tools.link_download_tool.BlobServiceClient",
            return_value=mock_blob_service,
        ) as MockBlobService, patch(
            "src.agents.tools.link_download_tool.aiohttp.ClientSession",
            return_value=mock_session,
        ) as MockSession:
            mock_cred_instance = AsyncMock()
            MockCred.return_value = mock_cred_instance

            result = await tool.process_email_links("email123", body)

        assert result.urls_detected == 1
        assert result.urls_attempted == 1
        assert len(result.downloaded_files) == 1
        assert result.failures == []

        downloaded = result.downloaded_files[0]
        assert downloaded.path == "email123/report.pdf"
        assert downloaded.source == "link"
        assert downloaded.url == "https://host.com/report.pdf"
        assert downloaded.content_type == "application/pdf"

        # Verify blob upload was called
        mock_blob_client.upload_blob.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_http_error_produces_failure(self, tool):
        """A 404 response should produce a DownloadFailure, not crash."""
        body = "File: https://host.com/missing.pdf"

        # Mock aiohttp response with 404
        mock_response = MagicMock()
        mock_response.status = 404

        @asynccontextmanager
        async def fake_get(url):
            yield mock_response

        mock_session = MagicMock()
        mock_session.get = fake_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_blob_service = AsyncMock()
        mock_blob_service.__aenter__ = AsyncMock(return_value=mock_blob_service)
        mock_blob_service.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.agents.tools.link_download_tool.DefaultAzureCredential"
        ) as MockCred, patch(
            "src.agents.tools.link_download_tool.BlobServiceClient"
        ) as MockBlobService, patch(
            "src.agents.tools.link_download_tool.aiohttp.ClientSession"
        ) as MockSession:
            mock_cred_instance = AsyncMock()
            MockCred.return_value = mock_cred_instance
            MockBlobService.return_value = mock_blob_service
            MockSession.return_value = mock_session

            result = await tool.process_email_links("email123", body)

        assert result.urls_attempted == 1
        assert len(result.downloaded_files) == 0
        assert len(result.failures) == 1
        assert "404" in result.failures[0].error

    @pytest.mark.asyncio
    async def test_oversized_content_length_produces_failure(self, tool):
        """A response with Content-Length exceeding the limit should be rejected."""
        body = "Big file: https://host.com/huge.pdf"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.content_length = tool.max_file_size_bytes + 1
        mock_response.content_type = "application/pdf"

        @asynccontextmanager
        async def fake_get(url):
            yield mock_response

        mock_session = MagicMock()
        mock_session.get = fake_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_blob_service = AsyncMock()
        mock_blob_service.__aenter__ = AsyncMock(return_value=mock_blob_service)
        mock_blob_service.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "src.agents.tools.link_download_tool.DefaultAzureCredential"
        ) as MockCred, patch(
            "src.agents.tools.link_download_tool.BlobServiceClient"
        ) as MockBlobService, patch(
            "src.agents.tools.link_download_tool.aiohttp.ClientSession"
        ) as MockSession:
            mock_cred_instance = AsyncMock()
            MockCred.return_value = mock_cred_instance
            MockBlobService.return_value = mock_blob_service
            MockSession.return_value = mock_session

            result = await tool.process_email_links("email123", body)

        assert len(result.failures) == 1
        assert "exceeds limit" in result.failures[0].error


# =====================================================================
# Document Extension Regex
# =====================================================================

class TestDocumentExtensionRegex:
    """Validate the DOCUMENT_EXTENSION_RE pattern directly."""

    @pytest.mark.parametrize("path,expected", [
        ("/file.pdf", True),
        ("/file.PDF", True),
        ("/file.pdf?v=1", True),
        # Document extensions pass the broad URL pre-filter
        ("/file.docx", True),
        ("/file.doc", True),
        ("/file.xlsx", True),
        ("/file.xls", True),
        ("/file.csv", True),
        ("/file.pptx", True),
        ("/file.ppt", True),
        ("/file.txt", True),
        # Non-document types are still rejected by the URL pre-filter
        ("/file.zip", False),
        ("/file.html", False),
        ("/file.jpg", False),
        ("/file.png", False),
        ("/file", False),
    ])
    def test_extension_matching(self, path, expected):
        match = bool(DOCUMENT_EXTENSION_RE.search(path))
        assert match == expected, f"Expected {expected} for path '{path}'"


# =====================================================================
# US2: Failure Scenario Tests — Categorized Errors
# =====================================================================

class TestFailureCategorization:
    """US2 tests: verify categorized error_type and http_status on DownloadFailure."""

    @pytest.fixture
    def tool(self):
        """Create a LinkDownloadTool with a fake storage URL."""
        with patch.dict("os.environ", {"STORAGE_ACCOUNT_URL": "https://fake.blob.core.windows.net"}):
            return LinkDownloadTool()

    def _mock_blob_and_session(self, fake_get):
        """Return (mock_session, mock_blob_service) wired with fake_get."""
        mock_session = MagicMock()
        mock_session.get = fake_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = MagicMock()
        mock_blob_service.__aenter__ = AsyncMock(return_value=mock_blob_service)
        mock_blob_service.__aexit__ = AsyncMock(return_value=False)

        return mock_session, mock_blob_service

    # ---- HTTP error codes ----

    @pytest.mark.asyncio
    async def test_http_404_returns_categorized_failure(self, tool):
        """HTTP 404 should produce error_type='http_error' and http_status=404."""
        body = "File: https://host.com/missing.pdf"

        mock_response = MagicMock()
        mock_response.status = 404

        @asynccontextmanager
        async def fake_get(url):
            yield mock_response

        mock_session, mock_blob_service = self._mock_blob_and_session(fake_get)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links("email-404", body)

        assert len(result.failures) == 1
        f = result.failures[0]
        assert f.error_type == "http_error"
        assert f.http_status == 404
        assert "404" in f.error
        assert f.url == "https://host.com/missing.pdf"
        assert f.attempted_at  # ISO 8601 present

    @pytest.mark.asyncio
    async def test_http_500_returns_categorized_failure(self, tool):
        """HTTP 500 should produce error_type='http_error' and http_status=500."""
        body = "File: https://host.com/broken.pdf"

        mock_response = MagicMock()
        mock_response.status = 500

        @asynccontextmanager
        async def fake_get(url):
            yield mock_response

        mock_session, mock_blob_service = self._mock_blob_and_session(fake_get)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links("email-500", body)

        assert len(result.failures) == 1
        f = result.failures[0]
        assert f.error_type == "http_error"
        assert f.http_status == 500

    # ---- Timeout ----

    @pytest.mark.asyncio
    async def test_timeout_returns_categorized_failure(self, tool):
        """asyncio.TimeoutError should produce error_type='timeout'."""
        body = "File: https://slow.com/report.pdf"

        @asynccontextmanager
        async def fake_get(url):
            raise asyncio.TimeoutError()
            yield  # pragma: no cover — makes it a generator

        mock_session, mock_blob_service = self._mock_blob_and_session(fake_get)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links("email-timeout", body)

        assert len(result.failures) == 1
        f = result.failures[0]
        assert f.error_type == "timeout"
        assert f.http_status is None
        assert "timed out" in f.error.lower()

    # ---- Content-type rejection ----

    @pytest.mark.asyncio
    async def test_html_content_type_rejected(self, tool):
        """text/html response should produce error_type='content_type_rejected'."""
        body = "File: https://host.com/report.pdf"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.content_type = "text/html; charset=utf-8"

        @asynccontextmanager
        async def fake_get(url):
            yield mock_response

        mock_session, mock_blob_service = self._mock_blob_and_session(fake_get)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links("email-html", body)

        assert len(result.failures) == 1
        f = result.failures[0]
        assert f.error_type == "content_type_rejected"
        assert "text/html" in f.error.lower()
        assert f.http_status is None

    @pytest.mark.asyncio
    async def test_image_content_type_rejected(self, tool):
        """image/jpeg should be rejected as a non-document MIME type."""
        body = "File: https://host.com/report.pdf"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.content_type = "image/jpeg"

        @asynccontextmanager
        async def fake_get(url):
            yield mock_response

        mock_session, mock_blob_service = self._mock_blob_and_session(fake_get)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links("email-img", body)

        assert len(result.failures) == 1
        assert result.failures[0].error_type == "content_type_rejected"

    # ---- Size exceeded ----

    @pytest.mark.asyncio
    async def test_size_exceeded_via_content_length(self, tool):
        """Content-Length exceeding max should produce error_type='size_exceeded'."""
        body = "File: https://host.com/huge.pdf"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.content_type = "application/pdf"
        mock_response.content_length = tool.max_file_size_bytes + 1

        @asynccontextmanager
        async def fake_get(url):
            yield mock_response

        mock_session, mock_blob_service = self._mock_blob_and_session(fake_get)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links("email-big", body)

        assert len(result.failures) == 1
        f = result.failures[0]
        assert f.error_type == "size_exceeded"
        assert "exceeds limit" in f.error

    @pytest.mark.asyncio
    async def test_size_exceeded_during_streaming(self, tool):
        """Streaming body exceeding max should abort and produce error_type='size_exceeded'."""
        body = "File: https://host.com/sneaky.pdf"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.content_type = "application/pdf"
        mock_response.content_length = None  # server doesn't announce size
        mock_response.content_disposition = None

        # Produce chunks that exceed the limit
        chunk = b"x" * (tool.max_file_size_bytes + 1)

        async def fake_iter_chunked(size):
            yield chunk

        mock_response.content.iter_chunked = fake_iter_chunked

        @asynccontextmanager
        async def fake_get(url):
            yield mock_response

        mock_session, mock_blob_service = self._mock_blob_and_session(fake_get)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links("email-stream-big", body)

        assert len(result.failures) == 1
        assert result.failures[0].error_type == "size_exceeded"
        assert "streaming" in result.failures[0].error.lower()

    # ---- Network error ----

    @pytest.mark.asyncio
    async def test_network_error_returns_categorized_failure(self, tool):
        """aiohttp.ClientError should produce error_type='network_error'."""
        body = "File: https://unreachable.com/report.pdf"

        @asynccontextmanager
        async def fake_get(url):
            raise aiohttp.ClientError("Connection refused")
            yield  # pragma: no cover

        mock_session, mock_blob_service = self._mock_blob_and_session(fake_get)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links("email-net", body)

        assert len(result.failures) == 1
        f = result.failures[0]
        assert f.error_type == "network_error"
        assert f.http_status is None

    # ---- Partial results: email processing continues ----

    @pytest.mark.asyncio
    async def test_partial_results_one_success_one_failure(self, tool):
        """An email with one good link and one bad link should produce both results."""
        body = (
            "Good: https://host.com/good.pdf\n"
            "Bad: https://host.com/bad.pdf"
        )
        pdf_bytes = b"%PDF-1.4 test"

        call_count = 0

        @asynccontextmanager
        async def fake_get(url):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if "good" in url:
                resp.status = 200
                resp.content_type = "application/pdf"
                resp.content_length = len(pdf_bytes)
                resp.content_disposition = None

                async def fake_iter(size):
                    yield pdf_bytes

                resp.content.iter_chunked = fake_iter
            else:
                resp.status = 404
            yield resp

        mock_session, mock_blob_service = self._mock_blob_and_session(fake_get)

        # Wire up blob upload for the successful download
        mock_blob_client = AsyncMock()
        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client
        mock_blob_service.get_container_client.return_value = mock_container_client

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links("email-partial", body)

        assert result.urls_attempted == 2
        assert len(result.downloaded_files) == 1
        assert len(result.failures) == 1

        # Success
        assert result.downloaded_files[0].url == "https://host.com/good.pdf"
        assert result.downloaded_files[0].source == "link"

        # Failure
        assert result.failures[0].url == "https://host.com/bad.pdf"
        assert result.failures[0].error_type == "http_error"
        assert result.failures[0].http_status == 404

        # Blob upload called only for the successful one
        mock_blob_client.upload_blob.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_failures_still_returns_result(self, tool):
        """Even if all downloads fail, processing should complete (not raise)."""
        body = (
            "File 1: https://host.com/a.pdf\n"
            "File 2: https://host.com/b.pdf"
        )

        @asynccontextmanager
        async def fake_get(url):
            resp = MagicMock()
            resp.status = 503
            yield resp

        mock_session, mock_blob_service = self._mock_blob_and_session(fake_get)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links("email-allfail", body)

        assert result.urls_attempted == 2
        assert result.downloaded_files == []
        assert len(result.failures) == 2
        assert all(f.error_type == "http_error" for f in result.failures)


# =====================================================================
# US2: DownloadFailure Dataclass Fields
# =====================================================================

class TestDownloadFailureFields:
    """Verify DownloadFailure defaults and field presence."""

    def test_default_error_type(self):
        f = DownloadFailure(url="https://x.com/f.pdf", error="oops", attempted_at="2024-01-01T00:00:00Z")
        assert f.error_type == "unknown"
        assert f.http_status is None

    def test_explicit_fields(self):
        f = DownloadFailure(
            url="https://x.com/f.pdf",
            error="HTTP 500",
            attempted_at="2024-01-01T00:00:00Z",
            error_type="http_error",
            http_status=500,
        )
        assert f.error_type == "http_error"
        assert f.http_status == 500


# =====================================================================
# Direct HTTPS Fallback (IDNA bypass)
# =====================================================================

class TestDirectHttpsFallback:
    """Tests for _download_with_urllib_fallback (direct HTTPS with IDNA bypass).

    The fallback resolves the hostname to an IP using bytes (bypassing
    Python's encodings.idna codec) and connects via raw socket + SSL SNI.
    """

    @pytest.fixture
    def tool(self):
        with patch.dict("os.environ", {"STORAGE_ACCOUNT_URL": "https://mock.blob.core.windows.net"}):
            return LinkDownloadTool()

    @pytest.mark.asyncio
    async def test_idna_error_triggers_fallback(self, tool):
        """When aiohttp raises an IDNA error, the fallback should be invoked."""
        body = "File: https://stpauldj5463027136334086.blob.core.windows.net/test-downloads/report.pdf"

        # On Azure App Service, IDNA errors surface as UnicodeError from yarl
        idna_exc = UnicodeError("encoding with 'idna' codec failed (UnicodeError: label empty or too long)")

        @asynccontextmanager
        async def fake_get(url):
            raise idna_exc
            yield  # pragma: no cover

        mock_session = MagicMock()
        mock_session.get = fake_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        mock_blob_service = MagicMock()
        mock_blob_service.__aenter__ = AsyncMock(return_value=mock_blob_service)
        mock_blob_service.__aexit__ = AsyncMock()

        fallback_called = False
        original_fallback = tool._download_with_urllib_fallback

        async def tracked_fallback(*args, **kwargs):
            nonlocal fallback_called
            fallback_called = True
            return await original_fallback(*args, **kwargs)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session), \
             patch.object(tool, "_download_with_urllib_fallback", side_effect=tracked_fallback):
            MockCred.return_value = AsyncMock()
            await tool.process_email_links("email-idna", body)

        assert fallback_called, "IDNA error should trigger fallback"

    @pytest.mark.asyncio
    async def test_fallback_dns_resolution_uses_bytes(self, tool):
        """Fallback should resolve hostname using bytes to bypass IDNA codec."""
        import socket as _socket

        url = "https://stpauldj5463027136334086.blob.core.windows.net/test-downloads/report.pdf"
        result = LinkDownloadResult()
        result.urls_attempted = 1

        # Track getaddrinfo calls
        original_getaddrinfo = _socket.getaddrinfo
        getaddrinfo_args = []

        def mock_getaddrinfo(host, *args, **kwargs):
            getaddrinfo_args.append(host)
            # Return a valid result for the bytes hostname
            return [(_socket.AF_INET, _socket.SOCK_STREAM, 0, "", ("20.60.234.4", 443))]

        mock_blob_service = MagicMock()
        mock_blob_service.__aenter__ = AsyncMock(return_value=mock_blob_service)
        mock_blob_service.__aexit__ = AsyncMock()

        # Mock the downstream socket/SSL/HTTP calls
        mock_ssl_sock = MagicMock()
        mock_conn_response = MagicMock()
        mock_conn_response.status = 200
        mock_conn_response.getheader = lambda h, d=None: "application/pdf" if h == "Content-Type" else d
        mock_conn_response.read = lambda: b"%PDF-1.4 fake content"

        with patch("src.agents.tools.link_download_tool.socket.getaddrinfo", side_effect=mock_getaddrinfo), \
             patch("src.agents.tools.link_download_tool.socket.create_connection", return_value=MagicMock()), \
             patch("src.agents.tools.link_download_tool.ssl.create_default_context") as mock_ssl_ctx, \
             patch("src.agents.tools.link_download_tool.http.client.HTTPSConnection") as MockConn:
            mock_ssl_ctx.return_value.wrap_socket.return_value = mock_ssl_sock
            mock_http_conn = MagicMock()
            mock_http_conn.getresponse.return_value = mock_conn_response
            MockConn.return_value = mock_http_conn

            # Mock blob upload
            mock_container = MagicMock()
            mock_blob_client = MagicMock()
            mock_blob_client.upload_blob = AsyncMock()
            mock_container.get_blob_client.return_value = mock_blob_client
            mock_blob_service.get_container_client.return_value = mock_container

            downloaded = await tool._download_with_urllib_fallback(
                mock_blob_service, "email-test", url, result,
            )

        # Verify DNS resolution was called with bytes (IDNA bypass)
        assert len(getaddrinfo_args) > 0, "getaddrinfo should have been called"
        assert isinstance(getaddrinfo_args[0], bytes), (
            f"hostname should be bytes to bypass IDNA, got {type(getaddrinfo_args[0])}"
        )
        assert getaddrinfo_args[0] == b"stpauldj5463027136334086.blob.core.windows.net"

        # Verify download succeeded
        assert downloaded is not None
        assert downloaded.source == "link"
        assert downloaded.content_type == "application/pdf"
        assert "report.pdf" in downloaded.path

    @pytest.mark.asyncio
    async def test_fallback_rejects_non_allowed_content_type(self, tool):
        """Fallback should reject content types not in the allowed list."""
        url = "https://stpauldj5463027136334086.blob.core.windows.net/test-downloads/data.csv"
        result = LinkDownloadResult()

        mock_blob_service = MagicMock()
        mock_conn_response = MagicMock()
        mock_conn_response.status = 200
        mock_conn_response.getheader = lambda h, d=None: "text/csv" if h == "Content-Type" else d

        with patch("src.agents.tools.link_download_tool.socket.getaddrinfo",
                    return_value=[(2, 1, 0, "", ("20.60.234.4", 443))]), \
             patch("src.agents.tools.link_download_tool.socket.create_connection", return_value=MagicMock()), \
             patch("src.agents.tools.link_download_tool.ssl.create_default_context") as mock_ssl_ctx, \
             patch("src.agents.tools.link_download_tool.http.client.HTTPSConnection") as MockConn:
            mock_ssl_ctx.return_value.wrap_socket.return_value = MagicMock()
            mock_http_conn = MagicMock()
            mock_http_conn.getresponse.return_value = mock_conn_response
            MockConn.return_value = mock_http_conn

            downloaded = await tool._download_with_urllib_fallback(
                mock_blob_service, "email-csv", url, result,
            )

        assert downloaded is None
        assert len(result.failures) == 1
        assert result.failures[0].error_type == "content_type_not_allowed"

    @pytest.mark.asyncio
    async def test_fallback_handles_http_error(self, tool):
        """Fallback returns failure for non-200 status codes."""
        url = "https://stpauldj5463027136334086.blob.core.windows.net/test-downloads/missing.pdf"
        result = LinkDownloadResult()

        mock_blob_service = MagicMock()
        mock_conn_response = MagicMock()
        mock_conn_response.status = 404

        with patch("src.agents.tools.link_download_tool.socket.getaddrinfo",
                    return_value=[(2, 1, 0, "", ("20.60.234.4", 443))]), \
             patch("src.agents.tools.link_download_tool.socket.create_connection", return_value=MagicMock()), \
             patch("src.agents.tools.link_download_tool.ssl.create_default_context") as mock_ssl_ctx, \
             patch("src.agents.tools.link_download_tool.http.client.HTTPSConnection") as MockConn:
            mock_ssl_ctx.return_value.wrap_socket.return_value = MagicMock()
            mock_http_conn = MagicMock()
            mock_http_conn.getresponse.return_value = mock_conn_response
            MockConn.return_value = mock_http_conn

            downloaded = await tool._download_with_urllib_fallback(
                mock_blob_service, "email-404", url, result,
            )

        assert downloaded is None
        assert len(result.failures) == 1
        assert result.failures[0].error_type == "http_error"
        assert "404" in result.failures[0].error

    @pytest.mark.asyncio
    async def test_fallback_handles_dns_failure(self, tool):
        """Fallback should record failure if DNS resolution fails."""
        url = "https://nonexistent-host-12345.example.com/test.pdf"
        result = LinkDownloadResult()

        mock_blob_service = MagicMock()

        with patch("src.agents.tools.link_download_tool.socket.getaddrinfo",
                    side_effect=socket.gaierror("Name or service not known")):
            downloaded = await tool._download_with_urllib_fallback(
                mock_blob_service, "email-dns", url, result,
            )

        assert downloaded is None
        assert len(result.failures) == 1
        assert "direct HTTPS fallback failed" in result.failures[0].error

    @pytest.mark.asyncio
    async def test_fallback_successful_pdf_download(self, tool):
        """Fallback should download, upload to blob, and return DownloadedFile."""
        url = "https://stpauldj5463027136334086.blob.core.windows.net/test-downloads/Capital_Call.pdf"
        result = LinkDownloadResult()
        pdf_data = b"%PDF-1.4 test content for capital call"

        mock_blob_service = MagicMock()
        mock_container = MagicMock()
        mock_blob_client = MagicMock()
        mock_blob_client.upload_blob = AsyncMock()
        mock_container.get_blob_client.return_value = mock_blob_client
        mock_blob_service.get_container_client.return_value = mock_container

        mock_conn_response = MagicMock()
        mock_conn_response.status = 200
        mock_conn_response.getheader = lambda h, d=None: "application/pdf" if h == "Content-Type" else d
        mock_conn_response.read = lambda: pdf_data

        with patch("src.agents.tools.link_download_tool.socket.getaddrinfo",
                    return_value=[(2, 1, 0, "", ("20.60.234.4", 443))]), \
             patch("src.agents.tools.link_download_tool.socket.create_connection", return_value=MagicMock()), \
             patch("src.agents.tools.link_download_tool.ssl.create_default_context") as mock_ssl_ctx, \
             patch("src.agents.tools.link_download_tool.http.client.HTTPSConnection") as MockConn:
            mock_ssl_ctx.return_value.wrap_socket.return_value = MagicMock()
            mock_http_conn = MagicMock()
            mock_http_conn.getresponse.return_value = mock_conn_response
            MockConn.return_value = mock_http_conn

            downloaded = await tool._download_with_urllib_fallback(
                mock_blob_service, "email-success", url, result,
            )

        assert downloaded is not None
        assert downloaded.source == "link"
        assert downloaded.content_type == "application/pdf"
        assert "Capital_Call.pdf" in downloaded.path
        assert downloaded.content_md5 is not None
        assert len(result.failures) == 0

        # Verify blob upload was called
        mock_blob_client.upload_blob.assert_called_once()
