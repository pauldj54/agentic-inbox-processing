"""
Unit tests for PE event deduplication key generation (_generate_dedup_key).

The dedup key is intentionally narrow: it identifies a notice by
`event_type | fund_name | investor | due_date`. `pe_company` and `amount`
are deliberately NOT part of the key.
"""

from unittest.mock import patch


class TestGenerateDedupKey:
    """Tests for CosmosDBTools._generate_dedup_key (4-field minimum key)."""

    def _create_cosmos_tools(self):
        env = {"COSMOS_ENDPOINT": "https://fake-cosmos.documents.azure.com"}
        with patch.dict("os.environ", env, clear=False):
            with patch("src.agents.tools.cosmos_tools.DefaultAzureCredential"):
                from src.agents.tools.cosmos_tools import CosmosDBTools
                tools = CosmosDBTools(
                    endpoint="https://fake-cosmos.documents.azure.com",
                    database_name="test-db",
                )
        return tools

    def test_same_inputs_produce_same_key(self):
        tools = self._create_cosmos_tools()
        kwargs = dict(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
            due_date="2026-03-15",
            investor="Anna Keller",
        )
        assert tools._generate_dedup_key(**kwargs) == tools._generate_dedup_key(**kwargs)

    def test_different_investor_produces_different_key(self):
        tools = self._create_cosmos_tools()
        base = dict(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
            due_date="2026-03-15",
        )
        assert tools._generate_dedup_key(**base, investor="Anna Keller") != \
            tools._generate_dedup_key(**base, investor="Sophie Laurent")

    def test_missing_investor_is_not_silently_defaulted(self):
        """Regression guard: previous implementation defaulted missing investors
        to 'Zava Private Bank', silently merging unrelated events."""
        tools = self._create_cosmos_tools()
        base = dict(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
            due_date="2026-03-15",
        )
        key_none = tools._generate_dedup_key(**base, investor=None)
        key_zava = tools._generate_dedup_key(**base, investor="Zava Private Bank")
        key_anna = tools._generate_dedup_key(**base, investor="Anna Keller")
        assert key_none != key_zava
        assert key_none != key_anna
        assert key_zava != key_anna

    def test_pe_company_is_ignored_by_key(self):
        """pe_company is derivable from fund_name and intentionally excluded."""
        tools = self._create_cosmos_tools()
        base = dict(
            fund_name="Alpha Fund III",
            event_type="Capital Call",
            due_date="2026-03-15",
            investor="Anna Keller",
        )
        key_a = tools._generate_dedup_key(pe_company="Alpha Capital", **base)
        key_b = tools._generate_dedup_key(pe_company="Totally Different GP", **base)
        assert key_a == key_b

    def test_amount_is_ignored_by_key(self):
        """A restated amount on the same notice must not create a phantom duplicate."""
        tools = self._create_cosmos_tools()
        base = dict(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
            due_date="2026-03-15",
            investor="Anna Keller",
        )
        key_a = tools._generate_dedup_key(amount="305400.00 EUR", **base)
        key_b = tools._generate_dedup_key(amount="305401.00 EUR", **base)
        assert key_a == key_b

    def test_different_fund_produces_different_key(self):
        tools = self._create_cosmos_tools()
        base = dict(
            pe_company="Alpha Capital",
            event_type="Capital Call",
            due_date="2026-03-15",
            investor="Anna Keller",
        )
        assert tools._generate_dedup_key(**base, fund_name="Alpha Fund III") != \
            tools._generate_dedup_key(**base, fund_name="Beta Growth Fund I")

    def test_different_event_type_produces_different_key(self):
        tools = self._create_cosmos_tools()
        base = dict(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            due_date="2026-03-15",
            investor="Anna Keller",
        )
        assert tools._generate_dedup_key(**base, event_type="Capital Call") != \
            tools._generate_dedup_key(**base, event_type="Distribution Notice")

    def test_different_due_date_produces_different_key(self):
        tools = self._create_cosmos_tools()
        base = dict(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
            investor="Anna Keller",
        )
        assert tools._generate_dedup_key(**base, due_date="2026-03-15") != \
            tools._generate_dedup_key(**base, due_date="2026-04-15")

    def test_investor_case_and_whitespace_normalized(self):
        tools = self._create_cosmos_tools()
        base = dict(
            pe_company="Alpha Capital",
            fund_name="Alpha Fund III",
            event_type="Capital Call",
            due_date="2026-03-15",
        )
        key_normal = tools._generate_dedup_key(**base, investor="Anna Keller")
        key_messy = tools._generate_dedup_key(**base, investor="  anna   KELLER  ")
        assert key_normal == key_messy

    def test_key_is_16_char_hex(self):
        tools = self._create_cosmos_tools()
        key = tools._generate_dedup_key(
            pe_company="Beta Corp",
            fund_name="Beta Fund I",
            event_type="Distribution",
            due_date="2026-05-01",
            investor="Acme Pension Fund",
        )
        assert len(key) == 16
        assert all(c in "0123456789abcdef" for c in key)
