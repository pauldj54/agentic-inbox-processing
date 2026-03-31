"""
Unit tests for delivery tracking methods (Spec 006).

Tests cover:
  - find_by_content_hash: partition-scoped query, null-guard, no-match
  - increment_delivery_count: duplicate action, update action, history append
  - DownloadedFile.content_md5: field populated after download
"""

import base64
import hashlib
from copy import deepcopy
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.agents.tools.link_download_tool import DownloadedFile, LinkDownloadTool


# =====================================================================
# DownloadedFile content_md5 field
# =====================================================================

class TestDownloadedFileContentMd5:
    """DownloadedFile now carries an optional content_md5 field."""

    def test_default_none(self):
        f = DownloadedFile(path="a/b.pdf", source="link", url="https://x.com/b.pdf", content_type="application/pdf")
        assert f.content_md5 is None

    def test_explicit_value(self):
        f = DownloadedFile(
            path="a/b.pdf", source="link", url="https://x.com/b.pdf",
            content_type="application/pdf", content_md5="abc123==",
        )
        assert f.content_md5 == "abc123=="

    def test_md5_matches_expected(self):
        data = b"hello world"
        expected = base64.b64encode(hashlib.md5(data).digest()).decode()
        f = DownloadedFile(
            path="a/b.pdf", source="link", url="https://x.com/b.pdf",
            content_type="application/pdf", content_md5=expected,
        )
        assert f.content_md5 == expected


# =====================================================================
# find_by_content_hash
# =====================================================================

class TestFindByContentHash:
    """Tests for CosmosDBTools.find_by_content_hash()."""

    def _make_tools(self):
        """Create CosmosDBTools with mocked Cosmos client."""
        with patch.dict("os.environ", {
            "COSMOS_ENDPOINT": "https://fake.documents.azure.com:443/",
            "COSMOS_DATABASE": "test-db",
        }):
            from src.agents.tools.cosmos_tools import CosmosDBTools
            tools = CosmosDBTools()
        return tools

    def test_returns_none_for_null_hash(self):
        tools = self._make_tools()
        assert tools.find_by_content_hash("", "domain_2025-01") is None
        assert tools.find_by_content_hash(None, "domain_2025-01") is None

    @patch("src.agents.tools.cosmos_tools.CosmosClient")
    def test_returns_matching_record(self, mock_cosmos_cls):
        tools = self._make_tools()
        record = {"id": "msg-1", "contentHash": "abc==", "deliveryCount": 1}

        mock_container = MagicMock()
        mock_container.query_items.return_value = [record]
        mock_db = MagicMock()
        mock_db.get_container_client.return_value = mock_container
        mock_client = MagicMock()
        mock_client.get_database_client.return_value = mock_db
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_cosmos_cls.return_value = mock_client

        result = tools.find_by_content_hash("abc==", "domain_2025-01")
        assert result == record
        mock_container.query_items.assert_called_once()
        call_kwargs = mock_container.query_items.call_args
        assert "@contentHash" in call_kwargs.kwargs.get("query", call_kwargs.args[0] if call_kwargs.args else "")

    @patch("src.agents.tools.cosmos_tools.CosmosClient")
    def test_returns_none_when_no_match(self, mock_cosmos_cls):
        tools = self._make_tools()

        mock_container = MagicMock()
        mock_container.query_items.return_value = []
        mock_db = MagicMock()
        mock_db.get_container_client.return_value = mock_container
        mock_client = MagicMock()
        mock_client.get_database_client.return_value = mock_db
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_cosmos_cls.return_value = mock_client

        result = tools.find_by_content_hash("xyz==", "domain_2025-01")
        assert result is None


# =====================================================================
# increment_delivery_count
# =====================================================================

class TestIncrementDeliveryCount:
    """Tests for CosmosDBTools.increment_delivery_count()."""

    def _make_tools(self):
        with patch.dict("os.environ", {
            "COSMOS_ENDPOINT": "https://fake.documents.azure.com:443/",
            "COSMOS_DATABASE": "test-db",
        }):
            from src.agents.tools.cosmos_tools import CosmosDBTools
            tools = CosmosDBTools()
        return tools

    def _base_record(self):
        return {
            "id": "msg-1",
            "partitionKey": "domain_2025-01",
            "contentHash": "abc==",
            "version": 1,
            "deliveryCount": 1,
            "deliveryHistory": [
                {"deliveredAt": "2025-01-01T00:00:00", "contentHash": "abc==", "action": "new"}
            ],
            "lastDeliveredAt": "2025-01-01T00:00:00",
        }

    @patch("src.agents.tools.cosmos_tools.CosmosClient")
    def test_duplicate_increments_delivery_count(self, mock_cosmos_cls):
        tools = self._make_tools()
        record = self._base_record()

        mock_container = MagicMock()
        mock_container.upsert_item.side_effect = lambda item: item
        mock_db = MagicMock()
        mock_db.get_container_client.return_value = mock_container
        mock_client = MagicMock()
        mock_client.get_database_client.return_value = mock_db
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_cosmos_cls.return_value = mock_client

        result = tools.increment_delivery_count(record, "abc==", action="duplicate")

        assert result["deliveryCount"] == 2
        assert result["version"] == 1  # Version unchanged for duplicate
        assert len(result["deliveryHistory"]) == 2
        assert result["deliveryHistory"][-1]["action"] == "duplicate"

    @patch("src.agents.tools.cosmos_tools.CosmosClient")
    def test_update_increments_version(self, mock_cosmos_cls):
        tools = self._make_tools()
        record = self._base_record()

        mock_container = MagicMock()
        mock_container.upsert_item.side_effect = lambda item: item
        mock_db = MagicMock()
        mock_db.get_container_client.return_value = mock_container
        mock_client = MagicMock()
        mock_client.get_database_client.return_value = mock_db
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_cosmos_cls.return_value = mock_client

        result = tools.increment_delivery_count(record, "newHash==", action="update")

        assert result["deliveryCount"] == 2
        assert result["version"] == 2  # Version incremented for update
        assert result["contentHash"] == "newHash=="  # Hash updated
        assert len(result["deliveryHistory"]) == 2
        assert result["deliveryHistory"][-1]["action"] == "update"

    @patch("src.agents.tools.cosmos_tools.CosmosClient")
    def test_history_appended_correctly(self, mock_cosmos_cls):
        tools = self._make_tools()
        record = self._base_record()

        mock_container = MagicMock()
        mock_container.upsert_item.side_effect = lambda item: item
        mock_db = MagicMock()
        mock_db.get_container_client.return_value = mock_container
        mock_client = MagicMock()
        mock_client.get_database_client.return_value = mock_db
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_cosmos_cls.return_value = mock_client

        result = tools.increment_delivery_count(record, "abc==")

        entry = result["deliveryHistory"][-1]
        assert "deliveredAt" in entry
        assert entry["contentHash"] == "abc=="
        assert entry["action"] == "duplicate"
        assert result["lastDeliveredAt"] is not None
        assert "updatedAt" in result


# =====================================================================
# Link download delivery tracking (T016)
# =====================================================================

class TestLinkDownloadDeliveryTracking:
    """Tests for delivery tracking dedup in LinkDownloadTool.process_email_links."""

    @pytest.fixture
    def mock_cosmos_tools(self):
        tools = MagicMock()
        tools.find_by_content_hash.return_value = None
        tools.increment_delivery_count.side_effect = lambda rec, h, action="duplicate": rec
        return tools

    @pytest.mark.asyncio
    async def test_new_record_gets_tracking_fields(self, mock_cosmos_tools):
        """New download (no hash match, no filename match) → DownloadedFile has content_md5 set."""
        mock_cosmos_tools.find_by_content_hash.return_value = None
        mock_cosmos_tools.find_by_filename.return_value = None

        with patch.dict("os.environ", {"STORAGE_ACCOUNT_URL": "https://fake.blob.core.windows.net"}):
            tool = LinkDownloadTool(cosmos_tools=mock_cosmos_tools)

        # Patch _download_and_upload to return a fake DownloadedFile
        fake_file = DownloadedFile(
            path="msg-1/doc.pdf", source="link",
            url="https://example.com/doc.pdf",
            content_type="application/pdf",
            content_md5="abc123==",
        )
        with patch.object(tool, "_download_and_upload", return_value=fake_file):
            result = await tool.process_email_links(
                email_id="msg-1",
                email_body='<a href="https://example.com/doc.pdf">link</a>',
                partition_key="example.com_2026-03",
            )

        assert len(result.downloaded_files) == 1
        assert result.downloaded_files[0].content_md5 == "abc123=="
        mock_cosmos_tools.find_by_content_hash.assert_called_once_with("abc123==", "example.com_2026-03")
        mock_cosmos_tools.increment_delivery_count.assert_not_called()

    @pytest.mark.asyncio
    async def test_duplicate_increments_delivery_count(self, mock_cosmos_tools):
        """Duplicate download (hash match) → increment_delivery_count called."""
        existing_record = {
            "id": "msg-0", "contentHash": "abc123==", "deliveryCount": 1,
            "partitionKey": "example.com_2026-03",
        }
        mock_cosmos_tools.find_by_content_hash.return_value = existing_record

        with patch.dict("os.environ", {"STORAGE_ACCOUNT_URL": "https://fake.blob.core.windows.net"}):
            tool = LinkDownloadTool(cosmos_tools=mock_cosmos_tools)

        fake_file = DownloadedFile(
            path="msg-1/doc.pdf", source="link",
            url="https://example.com/doc.pdf",
            content_type="application/pdf",
            content_md5="abc123==",
        )
        with patch.object(tool, "_download_and_upload", return_value=fake_file):
            result = await tool.process_email_links(
                email_id="msg-1",
                email_body='<a href="https://example.com/doc.pdf">link</a>',
                partition_key="example.com_2026-03",
            )

        assert len(result.downloaded_files) == 1
        mock_cosmos_tools.increment_delivery_count.assert_called_once_with(
            existing_record, "abc123==", action="duplicate"
        )

    @pytest.mark.asyncio
    async def test_failed_download_skips_tracking(self, mock_cosmos_tools):
        """Failed download → no dedup check."""
        with patch.dict("os.environ", {"STORAGE_ACCOUNT_URL": "https://fake.blob.core.windows.net"}):
            tool = LinkDownloadTool(cosmos_tools=mock_cosmos_tools)

        with patch.object(tool, "_download_and_upload", return_value=None):
            result = await tool.process_email_links(
                email_id="msg-1",
                email_body='<a href="https://example.com/doc.pdf">link</a>',
                partition_key="example.com_2026-03",
            )

        assert len(result.downloaded_files) == 0
        mock_cosmos_tools.find_by_content_hash.assert_not_called()

    @pytest.mark.asyncio
    async def test_filename_match_triggers_content_update(self, mock_cosmos_tools):
        """No hash match but filename match with different hash → action='update'."""
        mock_cosmos_tools.find_by_content_hash.return_value = None
        existing_record = {
            "id": "msg-0", "contentHash": "oldhash==", "deliveryCount": 1,
            "version": 1, "partitionKey": "example.com_2026-03",
        }
        mock_cosmos_tools.find_by_filename.return_value = existing_record

        with patch.dict("os.environ", {"STORAGE_ACCOUNT_URL": "https://fake.blob.core.windows.net"}):
            tool = LinkDownloadTool(cosmos_tools=mock_cosmos_tools)

        fake_file = DownloadedFile(
            path="msg-1/doc.pdf", source="link",
            url="https://example.com/doc.pdf",
            content_type="application/pdf",
            content_md5="newhash==",
        )
        with patch.object(tool, "_download_and_upload", return_value=fake_file):
            result = await tool.process_email_links(
                email_id="msg-1",
                email_body='<a href="https://example.com/doc.pdf">link</a>',
                partition_key="example.com_2026-03",
            )

        assert len(result.downloaded_files) == 1
        mock_cosmos_tools.find_by_filename.assert_called_once_with("doc.pdf", "example.com_2026-03")
        mock_cosmos_tools.increment_delivery_count.assert_called_once_with(
            existing_record, "newhash==", action="update"
        )


# =====================================================================
# Content update version increment (T021)
# =====================================================================

class TestContentUpdateVersionIncrement:
    """Test that same-filename-different-hash increments version."""

    def _make_tools(self):
        with patch.dict("os.environ", {
            "COSMOS_ENDPOINT": "https://fake.documents.azure.com:443/",
            "COSMOS_DATABASE": "test-db",
        }):
            from src.agents.tools.cosmos_tools import CosmosDBTools
            tools = CosmosDBTools()
        return tools

    @patch("src.agents.tools.cosmos_tools.CosmosClient")
    def test_content_update_increments_version(self, mock_cosmos_cls):
        """Same filename but different hash → version incremented, hash updated."""
        tools = self._make_tools()
        record = {
            "id": "msg-1",
            "partitionKey": "domain_2025-01",
            "contentHash": "oldHash==",
            "version": 1,
            "deliveryCount": 1,
            "deliveryHistory": [
                {"deliveredAt": "2025-01-01T00:00:00", "contentHash": "oldHash==", "action": "new"}
            ],
            "lastDeliveredAt": "2025-01-01T00:00:00",
        }

        mock_container = MagicMock()
        mock_container.upsert_item.side_effect = lambda item: item
        mock_db = MagicMock()
        mock_db.get_container_client.return_value = mock_container
        mock_client = MagicMock()
        mock_client.get_database_client.return_value = mock_db
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_cosmos_cls.return_value = mock_client

        result = tools.increment_delivery_count(record, "newHash==", action="update")

        assert result["version"] == 2
        assert result["deliveryCount"] == 2
        assert result["contentHash"] == "newHash=="
        assert len(result["deliveryHistory"]) == 2
        assert result["deliveryHistory"][-1]["action"] == "update"
        assert result["deliveryHistory"][-1]["contentHash"] == "newHash=="
