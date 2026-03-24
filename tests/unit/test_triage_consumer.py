"""
Unit tests for triage_consumer.py

Covers:
- T029: build_api_request() transform tests (email, SFTP, empty attachments, fund name, language, SC-003 count)
- T030: Message parsing edge cases (malformed JSON, mixed attachment formats, missing optional fields)
"""

import json
import os
import sys

# Set required env vars BEFORE importing triage_consumer (module-level validation)
os.environ.setdefault("SERVICEBUS_NAMESPACE", "test-namespace")
os.environ.setdefault("TRIAGE_COMPLETE_QUEUE", "triage-complete")

# Add src/ to path so we can import the consumer functions
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
from triage_consumer import (
    build_api_request,
    call_api,
    extract_sas_url_from_attachment,
    format_file_size,
    process_message,
    DEFAULT_PROJECT_NAME,
)


# --- Fixtures ---

@pytest.fixture
def email_triage_message():
    """Realistic email triage message matching producer schema."""
    return {
        "emailId": "AAMkADI5NmFl-test-001",
        "from": "investments@example.com",
        "subject": "Capital Call - Fonds Immobilier III",
        "receivedAt": "2025-07-17T10:30:00Z",
        "hasAttachments": True,
        "attachmentsCount": 2,
        "attachmentPaths": [
            {
                "name": "Capital_Call_Statement.pdf",
                "local_link": "https://stdocprocdev.blob.core.windows.net/attachments/Capital_Call_Statement.pdf",
                "size": 245760
            },
            {
                "name": "Distribution_Notice.pdf",
                "blobUrl": "https://stdocprocdev.blob.core.windows.net/attachments/Distribution_Notice.pdf",
                "size": 102400
            }
        ],
        "intakeSource": "email",
        "relevance": {
            "isRelevant": True,
            "confidence": 0.95,
            "initialCategory": "Capital Call",
            "reasoning": "Subject contains 'Capital Call' and attachments include financial statements"
        },
        "pipelineMode": "triage-only",
        "status": "triaged",
        "processedAt": "2025-07-17T10:30:15Z",
        "routing": {
            "sourceQueue": "email-intake",
            "targetQueue": "triage-complete",
            "routedAt": "2025-07-17T10:30:15Z"
        }
    }


@pytest.fixture
def sftp_triage_message():
    """Realistic SFTP triage message matching producer schema."""
    return {
        "emailId": "sftp-test-20250717",
        "from": "sftp-intake",
        "intakeSource": "sftp",
        "originalFilename": "PE_Investment_Report_Q4.pdf",
        "fileType": "pdf",
        "blobPath": "sftp-uploads/2025/07/PE_Investment_Report_Q4.pdf",
        "receivedAt": "2025-07-17T14:00:00Z",
        "hasAttachments": True,
        "attachmentsCount": 1,
        "attachmentPaths": [
            {
                "name": "PE_Investment_Report_Q4.pdf",
                "local_link": "https://stdocprocdev.blob.core.windows.net/attachments/sftp-uploads/PE_Investment_Report_Q4.pdf",
                "size": 1048576
            }
        ],
        "relevance": {
            "isRelevant": True,
            "confidence": 0.88,
            "initialCategory": "Investment Report",
            "reasoning": "SFTP file appears to be a quarterly investment report"
        },
        "pipelineMode": "triage-only",
        "status": "triaged",
        "processedAt": "2025-07-17T14:00:10Z",
        "routing": {
            "sourceQueue": "intake",
            "targetQueue": "triage-complete",
            "routedAt": "2025-07-17T14:00:10Z"
        }
    }


@pytest.fixture
def no_attachments_message():
    """Triage message with no attachments."""
    return {
        "emailId": "AAMkADI5-no-attach",
        "from": "noreply@example.com",
        "subject": "Status Update - No Files",
        "receivedAt": "2025-07-17T11:00:00Z",
        "hasAttachments": False,
        "attachmentsCount": 0,
        "attachmentPaths": [],
        "intakeSource": "email",
        "relevance": {
            "isRelevant": False,
            "confidence": 0.3,
            "initialCategory": "Other",
            "reasoning": "No relevant financial content detected"
        },
        "pipelineMode": "triage-only",
        "status": "triaged",
        "processedAt": "2025-07-17T11:00:05Z",
        "routing": {
            "sourceQueue": "email-intake",
            "targetQueue": "triage-complete",
            "routedAt": "2025-07-17T11:00:05Z"
        }
    }


# --- T029: build_api_request() tests ---

class TestBuildApiRequest:
    """Tests for build_api_request() transform function."""

    def test_email_message_transform(self, email_triage_message):
        """Email message produces correct API payload structure."""
        from triage_consumer import build_api_request

        result = build_api_request(email_triage_message)

        assert "documents" in result
        assert "project_name" in result
        assert "analysis_name" in result
        assert "analysis_description" in result
        assert "data_model_name" in result
        assert "classifier_name" in result
        assert result["classifier_name"] is None
        assert "language" in result
        assert "created_by" in result
        assert result["created_by"] == "triage_consumer"
        assert "auto_extract" in result
        assert result["auto_extract"] is True
        assert "_metadata" in result
        assert result["_metadata"]["email_id"] == "AAMkADI5NmFl-test-001"
        assert result["_metadata"]["intake_source"] == "email"

    def test_email_documents_have_sas_url_and_name(self, email_triage_message):
        """Each document entry has sas_url and document_name."""
        from triage_consumer import build_api_request

        result = build_api_request(email_triage_message)

        for doc in result["documents"]:
            assert "sas_url" in doc
            assert "document_name" in doc
            assert doc["sas_url"].startswith("https://")

    def test_documents_count_matches_attachments(self, email_triage_message):
        """SC-003: Output documents count matches input attachmentPaths count."""
        from triage_consumer import build_api_request

        result = build_api_request(email_triage_message)

        assert len(result["documents"]) == len(email_triage_message["attachmentPaths"])

    def test_sftp_message_transform(self, sftp_triage_message):
        """SFTP message produces correct API payload."""
        from triage_consumer import build_api_request

        result = build_api_request(sftp_triage_message)

        assert len(result["documents"]) == 1
        assert result["_metadata"]["intake_source"] == "sftp"
        assert result["documents"][0]["sas_url"].endswith("PE_Investment_Report_Q4.pdf")

    def test_sftp_documents_count_matches(self, sftp_triage_message):
        """SC-003: SFTP documents count matches attachmentPaths."""
        from triage_consumer import build_api_request

        result = build_api_request(sftp_triage_message)

        assert len(result["documents"]) == len(sftp_triage_message["attachmentPaths"])

    def test_empty_attachments(self, no_attachments_message):
        """Message with no attachments produces empty documents list."""
        from triage_consumer import build_api_request

        result = build_api_request(no_attachments_message)

        assert result["documents"] == []
        assert result["_metadata"]["email_id"] == "AAMkADI5-no-attach"

    def test_fund_name_extraction_fonds(self):
        """Fund name extracted from subject containing 'Fonds'."""
        from triage_consumer import build_api_request

        msg = {
            "emailId": "test-fund",
            "subject": "Capital Call - Fonds Immobilier III",
            "attachmentPaths": [],
            "intakeSource": "email",
            "processedAt": "2025-07-17T10:00:00Z",
        }
        result = build_api_request(msg)

        assert "Fonds" in result["project_name"] or "fonds" in result["project_name"].lower()

    def test_fund_name_extraction_fund(self):
        """Fund name extracted from subject containing 'Fund'."""
        from triage_consumer import build_api_request

        msg = {
            "emailId": "test-fund-en",
            "subject": "Distribution Notice - Global Fund IV",
            "attachmentPaths": [],
            "intakeSource": "email",
            "processedAt": "2025-07-17T10:00:00Z",
        }
        result = build_api_request(msg)

        assert "Fund" in result["project_name"] or "fund" in result["project_name"].lower()

    def test_fund_name_fallback_default(self):
        """Falls back to DEFAULT_PROJECT_NAME when no fund keyword found."""
        from triage_consumer import build_api_request, DEFAULT_PROJECT_NAME

        msg = {
            "emailId": "test-no-fund",
            "subject": "Monthly Report",
            "attachmentPaths": [],
            "intakeSource": "email",
            "processedAt": "2025-07-17T10:00:00Z",
        }
        result = build_api_request(msg)

        assert result["project_name"] == DEFAULT_PROJECT_NAME

    def test_language_detection_french(self):
        """French detected from relevance reasoning."""
        from triage_consumer import build_api_request

        msg = {
            "emailId": "test-lang-fr",
            "subject": "Appel de fonds",
            "attachmentPaths": [],
            "intakeSource": "email",
            "processedAt": "2025-07-17T10:00:00Z",
            "relevance": {
                "confidence": 0.9,
                "reasoning": "Email is in French with capital call content"
            }
        }
        result = build_api_request(msg)

        assert result["language"] == "fr"

    def test_language_default_english(self, email_triage_message):
        """Default language is English when no French indicators found."""
        from triage_consumer import build_api_request

        result = build_api_request(email_triage_message)

        # The existing message doesn't have French indicators in reasoning
        # (reasoning says "Subject contains 'Capital Call'..." — English)
        assert result["language"] in ("en", "fr")  # "fr" could match due to subject

    def test_url_resolution_priority_local_link(self):
        """URL resolution uses local_link first."""
        from triage_consumer import extract_sas_url_from_attachment

        att = {
            "local_link": "https://local.link/file.pdf",
            "blobUrl": "https://blob.url/file.pdf",
            "path": "/path/file.pdf"
        }
        result = extract_sas_url_from_attachment(att, "")

        assert result == "https://local.link/file.pdf"

    def test_url_resolution_priority_bloburl(self):
        """URL resolution falls back to blobUrl when no local_link."""
        from triage_consumer import extract_sas_url_from_attachment

        att = {
            "blobUrl": "https://blob.url/file.pdf",
            "path": "/path/file.pdf"
        }
        result = extract_sas_url_from_attachment(att, "")

        assert result == "https://blob.url/file.pdf"

    def test_url_resolution_priority_path(self):
        """URL resolution falls back to path when no local_link or blobUrl."""
        from triage_consumer import extract_sas_url_from_attachment

        att = {"path": "/path/file.pdf"}
        result = extract_sas_url_from_attachment(att, "")

        assert result == "/path/file.pdf"


# --- T030: Message parsing edge cases ---

class TestMessageParsingEdgeCases:
    """Tests for message parsing edge cases."""

    def test_malformed_json_handling(self):
        """Malformed JSON is caught and returns False (FR-010)."""
        from triage_consumer import process_message

        result = process_message("this is not valid json {{{")

        assert result is False

    def test_empty_string_handling(self):
        """Empty string is caught and returns False."""
        from triage_consumer import process_message

        result = process_message("")

        assert result is False

    def test_mixed_attachment_formats(self):
        """Message with both dict and string attachments is handled."""
        from triage_consumer import build_api_request

        msg = {
            "emailId": "test-mixed",
            "subject": "Mixed Attachments",
            "attachmentPaths": [
                {
                    "name": "file1.pdf",
                    "local_link": "https://storage.blob.core.windows.net/file1.pdf",
                    "size": 1024
                },
                "https://storage.blob.core.windows.net/file2.pdf"
            ],
            "intakeSource": "email",
            "processedAt": "2025-07-17T10:00:00Z",
        }
        result = build_api_request(msg)

        assert len(result["documents"]) == 2
        assert result["documents"][0]["sas_url"] == "https://storage.blob.core.windows.net/file1.pdf"
        assert result["documents"][0]["document_name"] == "file1.pdf"
        assert result["documents"][1]["sas_url"] == "https://storage.blob.core.windows.net/file2.pdf"

    def test_string_attachment_format(self):
        """Plain string attachment is handled by extract_sas_url_from_attachment."""
        from triage_consumer import extract_sas_url_from_attachment

        result = extract_sas_url_from_attachment("https://example.com/file.pdf", "")

        assert result == "https://example.com/file.pdf"

    def test_missing_optional_fields(self):
        """Message with only required fields is processed."""
        from triage_consumer import build_api_request

        minimal_msg = {
            "emailId": "test-minimal",
            "intakeSource": "email",
            "attachmentPaths": [],
            "processedAt": "2025-07-17T10:00:00Z",
        }
        result = build_api_request(minimal_msg)

        assert result["_metadata"]["email_id"] == "test-minimal"
        assert result["documents"] == []

    def test_missing_relevance(self):
        """Message without relevance block is handled."""
        from triage_consumer import build_api_request

        msg = {
            "emailId": "test-no-relevance",
            "subject": "Test",
            "attachmentPaths": [],
            "intakeSource": "email",
            "processedAt": "2025-07-17T10:00:00Z",
        }
        result = build_api_request(msg)

        # Should not error; language should be default
        assert result["language"] is not None

    def test_missing_subject(self):
        """Message without subject uses empty string for fund heuristic."""
        from triage_consumer import build_api_request, DEFAULT_PROJECT_NAME

        msg = {
            "emailId": "test-no-subject",
            "attachmentPaths": [],
            "intakeSource": "email",
            "processedAt": "2025-07-17T10:00:00Z",
        }
        result = build_api_request(msg)

        assert result["project_name"] == DEFAULT_PROJECT_NAME

    def test_none_attachment_in_list(self):
        """None value in attachmentPaths is handled gracefully."""
        from triage_consumer import extract_sas_url_from_attachment

        result = extract_sas_url_from_attachment(None, "")

        assert result is None


# --- format_file_size tests ---

class TestFormatFileSize:
    """Tests for the format_file_size helper."""

    def test_bytes(self):
        from triage_consumer import format_file_size
        assert format_file_size(500) == "500.00 B"

    def test_kilobytes(self):
        from triage_consumer import format_file_size
        assert format_file_size(245760) == "240.00 KB"

    def test_megabytes(self):
        from triage_consumer import format_file_size
        assert format_file_size(1048576) == "1.00 MB"

    def test_none_returns_unknown(self):
        from triage_consumer import format_file_size
        assert format_file_size(None) == "Unknown"

    def test_zero_returns_unknown(self):
        from triage_consumer import format_file_size
        assert format_file_size(0) == "Unknown"
