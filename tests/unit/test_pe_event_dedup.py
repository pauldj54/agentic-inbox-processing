"""
Unit tests for PE event deduplication key generation (_generate_dedup_key).

Covers:
  - Investor field inclusion in dedup key
  - Different investors produce different keys
  - Default investor fallback ("zava private bank")
  - Normalization consistency (case, whitespace)
"""

import pytest
from unittest.mock import patch, MagicMock


class TestGenerateDedupKey:
    """Tests for CosmosDBTools._generate_dedup_key with investor parameter."""

    def _create_cosmos_tools(self):
        """Create a CosmosDBTools instance with mocked Azure clients."""
        env = {
            "COSMOS_ENDPOINT": "https://fake-cosmos.documents.azure.com",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch("src.agents.tools.cosmos_tools.DefaultAzureCredential"):
                from src.agents.tools.cosmos_tools import CosmosDBTools
                tools = CosmosDBTools(
                    endpoint="https://fake-cosmos.documents.azure.com",
                    database_name="test-db",
                )
        return tools

    def test_same_inputs_produce_same_key(self):
        """Identical inputs → identical dedup key."""
        tools = self._create_cosmos_tools()
        key1 = tools._generate_dedup_key(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
            amount="1000000",
            due_date="2026-03-15",
            investor="Zava Private Bank",
        )
        key2 = tools._generate_dedup_key(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
            amount="1000000",
            due_date="2026-03-15",
            investor="Zava Private Bank",
        )
        assert key1 == key2

    def test_different_investor_produces_different_key(self):
        """Different investor → different dedup key."""
        tools = self._create_cosmos_tools()
        base_args = dict(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
            amount="1000000",
            due_date="2026-03-15",
        )
        key_zava = tools._generate_dedup_key(**base_args, investor="Zava Private Bank")
        key_other = tools._generate_dedup_key(**base_args, investor="Calpers")
        assert key_zava != key_other

    def test_investor_none_defaults_to_zava(self):
        """investor=None → same key as explicit 'Zava Private Bank'."""
        tools = self._create_cosmos_tools()
        base_args = dict(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
            amount="1000000",
            due_date="2026-03-15",
        )
        key_none = tools._generate_dedup_key(**base_args, investor=None)
        key_zava = tools._generate_dedup_key(**base_args, investor="Zava Private Bank")
        assert key_none == key_zava

    def test_investor_case_insensitive(self):
        """Investor names differing only in case → same key."""
        tools = self._create_cosmos_tools()
        base_args = dict(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
        )
        key_lower = tools._generate_dedup_key(**base_args, investor="zava private bank")
        key_mixed = tools._generate_dedup_key(**base_args, investor="Zava Private Bank")
        assert key_lower == key_mixed

    def test_investor_whitespace_normalized(self):
        """Extra whitespace in investor name → same key."""
        tools = self._create_cosmos_tools()
        base_args = dict(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
        )
        key_normal = tools._generate_dedup_key(**base_args, investor="Zava Private Bank")
        key_spaces = tools._generate_dedup_key(**base_args, investor="  Zava  Private   Bank  ")
        assert key_normal == key_spaces

    def test_key_is_16_char_hex(self):
        """Dedup key is a 16-character hex string."""
        tools = self._create_cosmos_tools()
        key = tools._generate_dedup_key(
            pe_company="Beta Corp",
            fund_name="Beta Fund I",
            event_type="Distribution",
        )
        assert len(key) == 16
        assert all(c in "0123456789abcdef" for c in key)
