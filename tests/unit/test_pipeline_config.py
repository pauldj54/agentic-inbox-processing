"""
Unit tests for Pipeline Configuration (Feature 002-pipeline-config).

Tests cover:
  - Full-pipeline mode (default): classification step IS called, email routes
    to archival-pending or human-review.
  - Triage-only mode: classification step is NOT called, email is sent to
    the triage-complete queue.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# =====================================================================
# Pipeline Mode Routing
# =====================================================================

class TestPipelineModeRouting:
    """Tests for pipeline mode conditional branching in process_next_email()."""

    def _build_email_data(self) -> dict:
        """Build a minimal PE-relevant email payload for testing."""
        return {
            "emailId": "test-email-001",
            "from": "sender@example.com",
            "subject": "PE Capital Call Q1",
            "bodyText": "Please find the capital call notice attached.",
            "receivedAt": "2026-03-01T10:00:00Z",
            "hasAttachments": True,
            "attachmentsCount": 1,
            "attachmentPaths": [{"path": "test-email-001/notice.pdf", "source": "attachment"}],
        }

    def _build_relevance_result(self) -> dict:
        """Build a successful relevance-check result (PE-relevant)."""
        return {
            "is_relevant": True,
            "confidence": 0.92,
            "initial_category": "Capital Call",
            "reasoning": "Subject mentions capital call",
        }

    def _build_classification_result(self) -> dict:
        """Build a successful classification result."""
        return {
            "category": "Capital calls",
            "confidence": 0.88,
            "fund_name": "Alpha Fund III",
            "pe_company": "Alpha Capital",
            "reasoning": "Identified as capital call based on content",
            "key_evidence": ["capital call notice"],
        }

    def _create_agent(self, pipeline_mode: str):
        """Create an EmailClassificationAgent with mocked dependencies.

        All Azure SDK interactions are patched so no real connections are made.
        """
        env = {
            "PIPELINE_MODE": pipeline_mode,
            "AZURE_AI_PROJECT_ENDPOINT": "https://fake.endpoint.azure.com",
            "SERVICEBUS_NAMESPACE": "fake-sb-ns",
            "COSMOS_ENDPOINT": "https://fake-cosmos.documents.azure.com",
            "DOCUMENT_INTELLIGENCE_ENDPOINT": "https://fake-di.cognitiveservices.azure.com",
            "TRIAGE_COMPLETE_QUEUE": "triage-complete",
        }

        with patch.dict("os.environ", env, clear=False):
            with patch("src.agents.email_classifier_agent.DefaultAzureCredential"):
                with patch("src.agents.email_classifier_agent.AgentsClient"):
                    with patch("src.agents.email_classifier_agent.QueueTools") as MockQueueTools:
                        with patch("src.agents.email_classifier_agent.GraphAPITools"):
                            with patch("src.agents.email_classifier_agent.DocumentIntelligenceTool"):
                                with patch("src.agents.email_classifier_agent.CosmosDBTools") as MockCosmosTools:
                                    with patch("src.agents.email_classifier_agent.LinkDownloadTool"):
                                        from src.agents.email_classifier_agent import EmailClassificationAgent
                                        agent = EmailClassificationAgent()

        # Expose mocked tool instances for assertions
        agent._mock_queue_tools = agent.queue_tools
        agent._mock_cosmos_tools = agent.cosmos_tools

        return agent

    # ── T007: Full-mode runs classification ──

    @pytest.mark.asyncio
    async def test_full_mode_runs_classification(self):
        """PIPELINE_MODE=full → classification IS called, email routes normally."""
        agent = self._create_agent("full")

        email_data = self._build_email_data()
        relevance_result = self._build_relevance_result()
        classification_result = self._build_classification_result()

        # Mock queue receive to return our email
        agent.queue_tools.receive_email_from_intake.return_value = {
            "body": email_data,
        }
        agent.queue_tools.QUEUE_EMAIL_INTAKE = "email-intake"
        agent.queue_tools.triage_queue = "triage-complete"
        agent.queue_tools.route_email.return_value = "archival-pending"

        # Mock relevance → PE-relevant
        agent._check_relevance = AsyncMock(return_value=relevance_result)

        # Mock link download (no links)
        mock_link_result = MagicMock()
        mock_link_result.downloaded_files = []
        mock_link_result.failures = []
        mock_link_result.urls_detected = 0
        mock_link_result.urls_attempted = 0
        agent.link_download_tool.process_email_links = AsyncMock(return_value=mock_link_result)

        # Mock attachment processing
        agent._process_attachments = AsyncMock(return_value=[])

        # Mock classification → successful
        agent._classify_email = AsyncMock(return_value=classification_result)

        # Mock Cosmos operations
        agent.cosmos_tools.log_classification_event = MagicMock()
        agent.cosmos_tools.update_email_classification = MagicMock(return_value={})
        agent.cosmos_tools.get_email_document = MagicMock(return_value=None)
        agent.cosmos_tools.find_or_create_pe_event = MagicMock(
            return_value=({"id": "pe-event-1"}, False)
        )
        agent.cosmos_tools.store_extracted_content = MagicMock()
        agent.cosmos_tools.store_table_data = MagicMock()

        result = await agent.process_next_email()

        # Assertions: classification WAS called
        agent._classify_email.assert_called_once()

        # Assertions: email was routed via route_email (not triage queue)
        agent.queue_tools.route_email.assert_called_once()
        agent.queue_tools.send_to_triage_queue.assert_not_called()

        # Result contains full classification info
        assert result is not None
        assert result["step"] == "full_classification"
        assert result["category"] == "Capital calls"
        assert result["routed_to"] == "archival-pending"

    # ── T010: Triage-only skips classification ──

    @pytest.mark.asyncio
    async def test_triage_only_skips_classification(self):
        """PIPELINE_MODE=triage-only → classification NOT called, routes to triage-complete."""
        agent = self._create_agent("triage-only")

        email_data = self._build_email_data()
        relevance_result = self._build_relevance_result()

        # Mock queue receive to return our email
        agent.queue_tools.receive_email_from_intake.return_value = {
            "body": email_data,
        }
        agent.queue_tools.QUEUE_EMAIL_INTAKE = "email-intake"
        agent.queue_tools.triage_queue = "triage-complete"
        agent.queue_tools.send_to_triage_queue.return_value = "triage-complete"

        # Mock relevance → PE-relevant
        agent._check_relevance = AsyncMock(return_value=relevance_result)

        # Mock link download (no links)
        mock_link_result = MagicMock()
        mock_link_result.downloaded_files = []
        mock_link_result.failures = []
        mock_link_result.urls_detected = 0
        mock_link_result.urls_attempted = 0
        agent.link_download_tool.process_email_links = AsyncMock(return_value=mock_link_result)

        # Mock attachment processing
        agent._process_attachments = AsyncMock(return_value=[])

        # Mock classification — should NOT be called
        agent._classify_email = AsyncMock(return_value={})

        # Mock Cosmos operations
        agent.cosmos_tools.log_classification_event = MagicMock()
        agent.cosmos_tools.update_email_classification = MagicMock(return_value={})
        agent.cosmos_tools.get_email_document = MagicMock(return_value=None)

        result = await agent.process_next_email()

        # Assertions: classification was NOT called
        agent._classify_email.assert_not_called()

        # Assertions: email was sent to triage queue
        agent.queue_tools.send_to_triage_queue.assert_called_once()
        agent.queue_tools.route_email.assert_not_called()

        # Result confirms triage-only flow
        assert result is not None
        assert result["step"] == "triage_only"
        assert result["pipeline_mode"] == "triage-only"
        assert result["routed_to"] == "triage-complete"

        # Verify Cosmos was updated with triage pipeline details
        cosmos_calls = agent.cosmos_tools.update_email_classification.call_args_list
        # The final update should contain pipelineMode and stepsExecuted
        final_call = cosmos_calls[-1]
        details = final_call.kwargs.get("classification_details") or final_call[1].get("classification_details")
        assert details["pipelineMode"] == "triage-only"
        assert "classification" not in details["stepsExecuted"]
        assert "triage" in details["stepsExecuted"]
        assert "routing" in details["stepsExecuted"]
