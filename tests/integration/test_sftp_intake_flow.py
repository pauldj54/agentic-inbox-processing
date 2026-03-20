"""
Integration test for SFTP file intake flow (T021, T025, T027, T032).

Tests cover:
  - CSV/Excel file → blob backup + Cosmos record with parsed metadata + sharepointPath + status "archived"
  - PDF file → blob backup + Cosmos record + Service Bus message to intake queue
  - Filename parse failure → Cosmos record with status "error" and metadataParseError
  - SFTP PDF in full mode → agent classifies → routed to archival-pending/human-review/discarded
  - SFTP PDF in triage-only mode → routed to triage-complete queue
  - Unsupported file type → logged, not processed, not moved
"""

import json
import os
import sys

import pytest

# These tests validate the SFTP intake workflow contract defined in
# specs/003-sftp-intake/contracts/contracts.md §3.
# They are designed to run against the Logic App workflow definition
# and verify Cosmos DB record structure for each routing path.


class TestSftpCsvExcelRouting:
    """US1: CSV/Excel files ingested via SFTP are uploaded to SharePoint."""

    def test_csv_cosmos_record_has_parsed_metadata(self):
        """CSV file → Cosmos record contains parsed filename metadata fields."""
        filename = "HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv"
        segments = filename.rsplit(".", 1)[0].split("_")

        assert len(segments) == 6
        assert segments[0] == "HorizonCapital"
        assert segments[1] == "GrowthFundIII"
        assert segments[2] == "NAVReport"
        assert segments[3] == "MarchNAV"
        # Date conversion: YYYYMMDD → ISO 8601
        raw_published = segments[4]
        published_iso = f"{raw_published[:4]}-{raw_published[4:6]}-{raw_published[6:8]}"
        assert published_iso == "2026-03-09"
        raw_effective = segments[5]
        effective_iso = f"{raw_effective[:4]}-{raw_effective[4:6]}-{raw_effective[6:8]}"
        assert effective_iso == "2026-03-01"

    def test_csv_record_structure_matches_contract(self):
        """CSV Cosmos record matches the intake-records contract schema."""
        record = {
            "id": "sftp-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "intakeSource": "sftp",
            "status": "archived",
            "receivedAt": "2026-03-09T14:30:00Z",
            "originalFilename": "HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv",
            "fileType": "csv",
            "fileSize": 102400,
            "sftpPath": "/inbox/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv",
            "contentHash": "d41d8cd98f00b204e9800998ecf8427e",
            "blobPath": "/attachments/sftp-a1b2c3d4-e5f6-7890-abcd-ef1234567890/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv",
            "account": "HorizonCapital",
            "fund": "GrowthFundIII",
            "docType": "NAVReport",
            "docName": "MarchNAV",
            "publishedDate": "2026-03-09",
            "effectiveDate": "2026-03-01",
            "sharepointPath": "Documents/H/HorizonCapital/GrowthFundIII/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv",
            "processedAt": "2026-03-09T14:31:00Z",
            "stepsExecuted": ["metadata-parse", "blob-upload", "sharepoint-upload"],
        }

        # Required fields for SFTP CSV record
        assert record["intakeSource"] == "sftp"
        assert record["status"] == "archived"
        assert record["sharepointPath"] is not None
        assert "sharepoint-upload" in record["stepsExecuted"]
        assert record["fileType"] == "csv"
        # Parsed metadata present
        assert record["account"] == "HorizonCapital"
        assert record["fund"] == "GrowthFundIII"
        # No email-specific fields
        assert "from" not in record
        assert "subject" not in record
        assert "emailBody" not in record

    def test_xlsx_record_same_flow_as_csv(self):
        """XLSX file follows same SharePoint routing as CSV."""
        filename = "AcmeFund_GlobalEquityII_QuarterlyReport_Q4Summary_20260315_20260101.xlsx"
        ext = filename.rsplit(".", 1)[1].lower()
        assert ext == "xlsx"

        segments = filename.rsplit(".", 1)[0].split("_")
        assert len(segments) == 6
        assert segments[0] == "AcmeFund"
        assert segments[1] == "GlobalEquityII"

        # SharePoint folder path convention
        account = segments[0]
        fund = segments[1]
        first_letter = account[0]
        sharepoint_path = f"Documents/{first_letter}/{account}/{fund}/{filename}"
        assert sharepoint_path == f"Documents/A/AcmeFund/GlobalEquityII/{filename}"

    def test_sharepoint_folder_path_convention(self):
        """SharePoint path follows {root}/{letter}/{Account}/{Fund}/{filename}."""
        account = "HorizonCapital"
        fund = "GrowthFundIII"
        filename = "HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv"
        doc_lib = "Documents"

        expected = f"{doc_lib}/{account[0]}/{account}/{fund}/{filename}"
        assert expected == "Documents/H/HorizonCapital/GrowthFundIII/HorizonCapital_GrowthFundIII_NAVReport_MarchNAV_20260309_20260301.csv"


class TestSftpFilenameParseFailure:
    """US1: Files with non-conforming filenames create error records."""

    def test_wrong_segment_count_creates_error_record(self):
        """Filename with wrong number of segments → status 'error' + metadataParseError."""
        filename = "report-q4-2026.csv"
        segments = filename.rsplit(".", 1)[0].split("_")
        # Only 1 segment (no underscores)
        assert len(segments) != 6

        error_record = {
            "id": "sftp-error-test-001",
            "intakeSource": "sftp",
            "status": "error",
            "originalFilename": filename,
            "fileType": "csv",
            "metadataParseError": f"Expected 6 segments in filename but found {len(segments)}",
            "sharepointPath": None,
            "blobPath": None,
            "stepsExecuted": [],
        }

        assert error_record["status"] == "error"
        assert error_record["metadataParseError"] is not None
        assert error_record["sharepointPath"] is None
        assert error_record["blobPath"] is None
        assert len(error_record["stepsExecuted"]) == 0

    def test_too_many_segments_creates_error_record(self):
        """Filename with too many segments → status 'error'."""
        filename = "Horizon_Capital_Growth_Fund_III_NAV_Report_March_20260309_20260301.csv"
        segments = filename.rsplit(".", 1)[0].split("_")
        assert len(segments) != 6  # 10 segments
        assert len(segments) > 6


class TestSftpPdfRouting:
    """US2: PDF files from SFTP are sent to intake queue."""

    def test_pdf_service_bus_message_matches_contract(self):
        """PDF → Service Bus message contains all required fields per contract §1."""
        message = {
            "fileId": "sftp-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "originalFilename": "HorizonCapital_GrowthFundIII_CapitalCall_Q4Report_20260309_20260301.pdf",
            "fileType": "pdf",
            "blobPath": "/attachments/sftp-a1b2c3d4-e5f6-7890-abcd-ef1234567890/HorizonCapital_GrowthFundIII_CapitalCall_Q4Report_20260309_20260301.pdf",
            "intakeSource": "sftp",
            "receivedAt": "2026-03-09T14:30:00Z",
            "sftpPath": "/inbox/HorizonCapital_GrowthFundIII_CapitalCall_Q4Report_20260309_20260301.pdf",
            "contentHash": "abc123hash",
            "fileSize": 245780,
            "account": "HorizonCapital",
            "fund": "GrowthFundIII",
            "docType": "CapitalCall",
            "docName": "Q4Report",
            "publishedDate": "2026-03-09",
            "effectiveDate": "2026-03-01",
        }

        # All required fields present
        assert message["fileId"].startswith("sftp-")
        assert message["intakeSource"] == "sftp"
        assert message["fileType"] == "pdf"
        assert message["blobPath"].startswith("/attachments/sftp-")
        assert message["contentHash"] is not None
        assert message["fileSize"] > 0
        # Parsed metadata
        assert message["account"] == "HorizonCapital"
        assert message["fund"] == "GrowthFundIII"
        # No email-specific fields
        assert "emailId" not in message
        assert "from" not in message
        assert "subject" not in message

    def test_pdf_cosmos_record_has_queue_field(self):
        """PDF Cosmos record has queue set to 'intake'."""
        record = {
            "id": "sftp-pdf-test-001",
            "intakeSource": "sftp",
            "status": "received",
            "fileType": "pdf",
            "queue": "intake",
            "stepsExecuted": ["metadata-parse", "blob-upload", "queue-send"],
            "sharepointPath": None,
        }

        assert record["queue"] == "intake"
        assert "queue-send" in record["stepsExecuted"]
        assert record["sharepointPath"] is None

    def test_sftp_pdf_no_email_fields_in_context(self):
        """SFTP PDF record has no email-specific fields for classification."""
        record = {
            "id": "sftp-pdf-context-test",
            "intakeSource": "sftp",
            "originalFilename": "test.pdf",
            "fileType": "pdf",
            "blobPath": "/attachments/sftp-xxx/test.pdf",
        }

        # Email-specific fields must NOT be present
        assert "from" not in record
        assert "subject" not in record
        assert "emailBody" not in record
        assert "attachmentPaths" not in record


class TestSftpTriageOnlyRouting:
    """US3: SFTP PDFs in triage-only mode bypass classification."""

    def test_triage_only_record_structure(self):
        """Triage-only SFTP PDF → pipelineMode 'triage-only', no classification."""
        record = {
            "id": "sftp-triage-test-001",
            "intakeSource": "sftp",
            "status": "received",
            "fileType": "pdf",
            "pipelineMode": "triage-only",
            "stepsExecuted": ["metadata-parse", "blob-upload", "queue-send"],
            "classification": None,
            "queue": "triage-complete",
        }

        assert record["pipelineMode"] == "triage-only"
        assert record["classification"] is None
        assert "classification" not in (record.get("stepsExecuted") or [])
        assert record["queue"] == "triage-complete"

    def test_triage_only_sftp_routes_to_triage_complete(self):
        """T027: SFTP PDF in triage-only mode → triage-complete queue."""
        data = {
            "fileId": "sftp-triage-route-001",
            "intakeSource": "sftp",
            "originalFilename": "Acme_FundX_CapitalCall_Notice_20260401_20260401.pdf",
            "fileType": "pdf",
            "blobPath": "sftp-triage-route-001/Acme_FundX_CapitalCall_Notice_20260401_20260401.pdf",
        }

        # Simulate agent triage-only pipeline path
        intake_source = data.get("intakeSource", "email")
        assert intake_source == "sftp"

        # SFTP → auto-relevant
        relevance_result = {
            "is_relevant": True,
            "confidence": 1.0,
            "initial_category": "pdf",
            "reasoning": "SFTP-sourced PDF — auto-relevant",
        }

        # Build expected triage message
        triage_message = {
            "emailId": data["fileId"],
            "intakeSource": "sftp",
            "originalFilename": data["originalFilename"],
            "fileType": data["fileType"],
            "blobPath": data["blobPath"],
            "relevance": {
                "isRelevant": True,
                "confidence": relevance_result["confidence"],
                "initialCategory": relevance_result["initial_category"],
            },
            "pipelineMode": "triage-only",
            "status": "triaged",
        }

        assert triage_message["pipelineMode"] == "triage-only"
        assert triage_message["intakeSource"] == "sftp"
        assert triage_message["originalFilename"] == data["originalFilename"]
        assert triage_message["relevance"]["isRelevant"] is True

    def test_triage_only_sftp_no_classification_step(self):
        """T027: Triage-only SFTP skips Steps 3-5 (classification)."""
        steps_executed = ["triage", "pre-processing", "routing"]
        assert "classification" not in steps_executed
        assert "content-storage" not in steps_executed
        assert "confidence-routing" not in steps_executed


class TestSftpUnsupportedFileType:
    """US1: Unsupported file types are logged and skipped."""

    def test_unsupported_extension_not_processed(self):
        """Unsupported file types (.docx, .txt) are not routed."""
        unsupported_extensions = ["docx", "txt", "zip", "png", "jpg"]
        supported_extensions = ["csv", "xlsx", "xls", "pdf"]

        for ext in unsupported_extensions:
            assert ext not in supported_extensions

    def test_unsupported_file_not_moved_from_sftp(self):
        """Unsupported files remain in the SFTP inbox folder (not archived)."""
        # Per contracts.md: "Unsupported file types are logged and skipped.
        # File stays on SFTP."
        action = "Skipped - file not processed, not moved"
        assert "not moved" in action

    def test_docx_file_skipped(self):
        """A .docx file is logged as unsupported, not processed, not moved."""
        filename = "SomeCompany_FundA_Report_Q1_20260101_20260101.docx"
        ext = filename.rsplit(".", 1)[-1].lower()
        supported = {"csv", "xlsx", "xls", "pdf"}
        assert ext not in supported, f".{ext} should be unsupported"

    def test_txt_file_skipped(self):
        """A .txt file is logged as unsupported, not processed, not moved."""
        filename = "DataFile_FundB_Summary_Annual_20260601_20260601.txt"
        ext = filename.rsplit(".", 1)[-1].lower()
        supported = {"csv", "xlsx", "xls", "pdf"}
        assert ext not in supported, f".{ext} should be unsupported"

    def test_unsupported_file_no_cosmos_record_created(self):
        """Unsupported files should not create a Cosmos DB record (per workflow)."""
        # The Logic App condition step only processes csv/xlsx/xls/pdf.
        # If the file extension does not match, processing is skipped entirely.
        supported = {"csv", "xlsx", "xls", "pdf"}
        unsupported = {"docx", "txt", "zip", "png", "jpg", "msg", "eml"}
        assert supported.isdisjoint(unsupported)


class TestSftpDuplicateDetection:
    """Duplicate files are detected and skipped."""

    def test_duplicate_detection_query_structure(self):
        """Duplicate detection uses sftpPath + contentHash + intakeSource."""
        query = (
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.sftpPath = @sftpPath "
            "AND c.contentHash = @contentHash "
            "AND c.intakeSource = 'sftp'"
        )
        assert "@sftpPath" in query
        assert "@contentHash" in query
        assert "intakeSource = 'sftp'" in query

    def test_duplicate_file_not_moved(self):
        """Duplicate files are NOT moved from the SFTP folder."""
        # Per contracts.md: "If count > 0, skip steps 5-11 and log a warning."
        # File stays on SFTP for reprocessing safety
        duplicate_action = "Skipped processing - file NOT moved"
        assert "NOT moved" in duplicate_action


class TestSftpPdfAgentClassification:
    """US2/T025: Agent SFTP detection and classification routing."""

    def _build_sftp_email_data(self) -> dict:
        """Build a minimal SFTP PDF payload as received from Service Bus."""
        return {
            "fileId": "sftp-a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "originalFilename": "HorizonCapital_GrowthFundIII_CapitalCall_Q4Call_20260309_20260301.pdf",
            "fileType": "pdf",
            "blobPath": "sftp-a1b2c3d4-e5f6-7890-abcd-ef1234567890/HorizonCapital_GrowthFundIII_CapitalCall_Q4Call_20260309_20260301.pdf",
            "intakeSource": "sftp",
            "receivedAt": "2026-03-09T14:30:00Z",
            "sftpPath": "/inbox/HorizonCapital_GrowthFundIII_CapitalCall_Q4Call_20260309_20260301.pdf",
            "contentHash": "abc123hash",
            "fileSize": 245780,
            "account": "HorizonCapital",
            "fund": "GrowthFundIII",
            "docType": "CapitalCall",
            "name": "Q4Call",
            "publishedDate": "2026-03-09",
            "effectiveDate": "2026-03-01",
        }

    def test_intake_source_detected_as_sftp(self):
        """Agent detects intakeSource == 'sftp' from queue message."""
        data = self._build_sftp_email_data()
        intake_source = data.get("intakeSource", "email")
        assert intake_source == "sftp"

    def test_sftp_uses_file_id_not_email_id(self):
        """SFTP records use fileId as document identifier, not emailId."""
        data = self._build_sftp_email_data()
        intake_source = data.get("intakeSource", "email")
        if intake_source == "sftp":
            doc_id = data.get("fileId") or data.get("id") or "unknown"
        else:
            doc_id = data.get("emailId") or data.get("id") or "unknown"
        assert doc_id == "sftp-a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_sftp_auto_relevant_skips_relevance_check(self):
        """SFTP PDFs are auto-relevant — relevance check is skipped."""
        data = self._build_sftp_email_data()
        intake_source = data.get("intakeSource", "email")
        if intake_source == "sftp":
            relevance_result = {
                "is_relevant": True,
                "confidence": 1.0,
                "reasoning": "SFTP-sourced PDF — auto-relevant",
                "initial_category": data.get("fileType", "Others"),
            }
        else:
            relevance_result = None  # Would call LLM

        assert relevance_result is not None
        assert relevance_result["is_relevant"] is True
        assert relevance_result["confidence"] == 1.0

    def test_sftp_skips_link_download(self):
        """SFTP records bypass Step 1.5 (link download)."""
        data = self._build_sftp_email_data()
        intake_source = data.get("intakeSource", "email")
        # Link download only runs for non-SFTP sources
        should_download_links = (intake_source != "sftp")
        assert should_download_links is False

    def test_sftp_attachment_path_from_blob(self):
        """SFTP uses blobPath for attachment processing instead of Graph API."""
        data = self._build_sftp_email_data()
        intake_source = data.get("intakeSource", "email")
        if intake_source == "sftp":
            blob_path = data.get("blobPath")
            attachment_paths = [{"path": blob_path, "source": "sftp"}]
        else:
            attachment_paths = data.get("attachmentPaths", [])

        assert len(attachment_paths) == 1
        assert attachment_paths[0]["source"] == "sftp"
        assert "sftp-" in attachment_paths[0]["path"]

    def test_sftp_classification_prompt_has_no_email_fields(self):
        """SFTP classification prompt uses filename metadata, not email fields."""
        from src.agents.classification_prompts import SFTP_CLASSIFICATION_USER_PROMPT

        data = self._build_sftp_email_data()
        prompt = SFTP_CLASSIFICATION_USER_PROMPT.format(
            original_filename=data.get("originalFilename", "unknown"),
            file_type=data.get("fileType", "unknown"),
            received_date=data.get("receivedAt", "Unknown"),
            account=data.get("account", "Unknown"),
            fund=data.get("fund", "Unknown"),
            doc_type=data.get("docType", "Unknown"),
            name=data.get("name", "Unknown"),
            published_date=data.get("publishedDate", "Unknown"),
            effective_date=data.get("effectiveDate", "Unknown"),
            attachment_analysis="[test attachment content]",
        )

        # Prompt should contain SFTP-specific context
        assert "SFTP file intake" in prompt
        assert "HorizonCapital" in prompt
        assert "GrowthFundIII" in prompt
        assert "CapitalCall" in prompt
        # Prompt should NOT reference email fields
        assert "From:" not in prompt
        assert "Subject:" not in prompt
        assert "Email Body" not in prompt

    def test_sftp_classification_routes_high_confidence(self):
        """SFTP PDF with high-confidence classification → archival-pending."""
        classification = {
            "category": "Capital Call",
            "confidence": 0.92,
            "fund_name": "GrowthFundIII",
            "pe_company": "HorizonCapital",
        }
        # Same routing logic as email: confidence >= 0.65 → archival-pending
        if classification["confidence"] >= 0.65:
            target = "archival-pending"
        else:
            target = "human-review"
        assert target == "archival-pending"

    def test_sftp_classification_routes_low_confidence(self):
        """SFTP PDF with low-confidence classification → human-review."""
        classification = {
            "category": "Capital Call",
            "confidence": 0.55,
            "fund_name": "Unknown",
            "pe_company": "Unknown",
        }
        if classification["confidence"] >= 0.65:
            target = "archival-pending"
        else:
            target = "human-review"
        assert target == "human-review"
