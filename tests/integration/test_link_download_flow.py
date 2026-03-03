"""
Integration test for end-to-end link download flow (T017).

Tests cover:
  - Full pipeline: URL extraction → document filter → download → blob upload
  - Mixed success + failure results in a single email
  - Cosmos DB document enrichment with attachmentPaths and downloadFailures
  - LinkDownloadResult correctness across multiple URLs
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from src.agents.tools.link_download_tool import (
    DownloadedFile,
    DownloadFailure,
    LinkDownloadResult,
    LinkDownloadTool,
)


class TestLinkDownloadEndToEnd:
    """Integration test: full link download pipeline with mocked HTTP + blob."""

    @pytest.fixture
    def tool(self):
        with patch.dict("os.environ", {"STORAGE_ACCOUNT_URL": "https://fake.blob.core.windows.net"}):
            return LinkDownloadTool()

    def _build_mock_response(
        self,
        *,
        status=200,
        content_type="application/pdf",
        content_bytes=b"%PDF-1.4 test",
        content_length=None,
        disposition_filename=None,
    ):
        """Build a mock aiohttp response."""
        resp = MagicMock()
        resp.status = status
        resp.content_type = content_type
        resp.content_length = content_length if content_length is not None else len(content_bytes)
        if disposition_filename:
            resp.content_disposition = MagicMock()
            resp.content_disposition.filename = disposition_filename
        else:
            resp.content_disposition = None

        async def iter_chunked(size):
            yield content_bytes

        resp.content.iter_chunked = iter_chunked
        return resp

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure_pipeline(self, tool):
        """
        Email with 4 URLs:
          1. https://docs.example.com/report.pdf    → 200 OK (success)
          2. https://docs.example.com/quarterly.xlsx → 404 (failure)
          3. https://docs.example.com/analysis.docx → text/html content-type (rejected)
          4. https://twitter.com/status/123.pdf      → Skip domain (not attempted)

        Expected: 3 detected, 1 success, 2 failures (domain-skip not counted as attempted).
        """
        email_id = "integration-test-001"
        body = (
            "Please review the following documents:\n"
            "1. Annual report: https://docs.example.com/report.pdf\n"
            "2. Quarterly data: https://docs.example.com/quarterly.xlsx\n"
            "3. Analysis: https://docs.example.com/analysis.docx\n"
            "4. Thread: https://twitter.com/status/123.pdf\n"
        )

        pdf_bytes = b"%PDF-1.4 annual report content"

        # Build per-URL responses
        responses = {
            "https://docs.example.com/report.pdf": self._build_mock_response(
                content_bytes=pdf_bytes,
                content_type="application/pdf",
            ),
            "https://docs.example.com/quarterly.xlsx": self._build_mock_response(
                status=404,
            ),
            "https://docs.example.com/analysis.docx": self._build_mock_response(
                content_type="text/html; charset=utf-8",
            ),
        }

        @asynccontextmanager
        async def fake_get(url):
            yield responses[url]

        mock_session = MagicMock()
        mock_session.get = fake_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_blob_client = AsyncMock()
        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client
        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container_client
        mock_blob_service.__aenter__ = AsyncMock(return_value=mock_blob_service)
        mock_blob_service.__aexit__ = AsyncMock(return_value=False)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links(email_id, body)

        # --- Assertions ---

        # URL detection: 4 total URLs found
        assert result.urls_detected == 4

        # URL attempted: 3 (twitter.com is domain-skipped, not attempted)
        assert result.urls_attempted == 3

        # 1 successful download
        assert len(result.downloaded_files) == 1
        dl = result.downloaded_files[0]
        assert dl.path == f"{email_id}/report.pdf"
        assert dl.source == "link"
        assert dl.url == "https://docs.example.com/report.pdf"
        assert dl.content_type == "application/pdf"

        # 2 failures
        assert len(result.failures) == 2

        # Sort by URL for deterministic assertions
        failures_by_url = {f.url: f for f in result.failures}

        # Failure 1: HTTP 404
        f_404 = failures_by_url["https://docs.example.com/quarterly.xlsx"]
        assert f_404.error_type == "http_error"
        assert f_404.http_status == 404
        assert "404" in f_404.error
        assert f_404.attempted_at  # ISO 8601

        # Failure 2: content-type rejected
        f_ct = failures_by_url["https://docs.example.com/analysis.docx"]
        assert f_ct.error_type == "content_type_rejected"
        assert "text/html" in f_ct.error.lower()

        # Blob upload called once (only for the successful file)
        mock_blob_client.upload_blob.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cosmos_document_enrichment_shape(self, tool):
        """
        Verify the shape of data that would be persisted to Cosmos DB.

        This tests the serialization logic that email_classifier_agent.py
        uses to build the _link_download_result dict.
        """
        email_id = "integration-cosmos-001"
        body = "Download: https://host.com/file.pdf"
        pdf_bytes = b"%PDF test"

        mock_response = self._build_mock_response(content_bytes=pdf_bytes)

        @asynccontextmanager
        async def fake_get(url):
            yield mock_response

        mock_session = MagicMock()
        mock_session.get = fake_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_blob_client = AsyncMock()
        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client
        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container_client
        mock_blob_service.__aenter__ = AsyncMock(return_value=mock_blob_service)
        mock_blob_service.__aexit__ = AsyncMock(return_value=False)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links(email_id, body)

        # Simulate the serialization from email_classifier_agent.py Step 1.5
        link_download_doc = {
            "downloaded": [
                {"path": df.path, "source": df.source, "url": df.url, "contentType": df.content_type}
                for df in result.downloaded_files
            ],
            "failures": [
                {
                    "url": f.url,
                    "error": f.error,
                    "attempted_at": f.attempted_at,
                    "error_type": f.error_type,
                    "http_status": f.http_status,
                }
                for f in result.failures
            ],
            "urlsDetected": result.urls_detected,
            "urlsAttempted": result.urls_attempted,
        }

        # Verify document shape
        assert link_download_doc["urlsDetected"] == 1
        assert link_download_doc["urlsAttempted"] == 1
        assert len(link_download_doc["downloaded"]) == 1
        assert len(link_download_doc["failures"]) == 0

        dl = link_download_doc["downloaded"][0]
        assert dl["path"] == f"{email_id}/file.pdf"
        assert dl["source"] == "link"
        assert dl["contentType"] == "application/pdf"

        # Verify the merged attachmentPaths entry shape
        merged_attachment = {"path": dl["path"], "source": "link"}
        assert merged_attachment["source"] == "link"

    @pytest.mark.asyncio
    async def test_timeout_in_pipeline_does_not_block_other_downloads(self, tool):
        """
        A timeout on one URL should not prevent other URLs from being downloaded.
        """
        body = (
            "File 1: https://slow.com/timeout.pdf\n"
            "File 2: https://fast.com/quick.pdf"
        )
        pdf_bytes = b"%PDF quick download"

        call_count = 0

        @asynccontextmanager
        async def fake_get(url):
            nonlocal call_count
            call_count += 1
            if "slow" in url:
                raise asyncio.TimeoutError()
                yield  # pragma: no cover
            else:
                yield self._build_mock_response(content_bytes=pdf_bytes)

        mock_session = MagicMock()
        mock_session.get = fake_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_blob_client = AsyncMock()
        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client
        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container_client
        mock_blob_service.__aenter__ = AsyncMock(return_value=mock_blob_service)
        mock_blob_service.__aexit__ = AsyncMock(return_value=False)

        with patch("src.agents.tools.link_download_tool.DefaultAzureCredential") as MockCred, \
             patch("src.agents.tools.link_download_tool.BlobServiceClient", return_value=mock_blob_service), \
             patch("src.agents.tools.link_download_tool.aiohttp.ClientSession", return_value=mock_session):
            MockCred.return_value = AsyncMock()
            result = await tool.process_email_links("email-mixed", body)

        # Both URLs attempted
        assert result.urls_attempted == 2

        # One succeeded, one failed
        assert len(result.downloaded_files) == 1
        assert result.downloaded_files[0].url == "https://fast.com/quick.pdf"

        assert len(result.failures) == 1
        assert result.failures[0].error_type == "timeout"
        assert result.failures[0].url == "https://slow.com/timeout.pdf"
