"""Unit tests for deterministic PE document event extraction."""

from src.agents.email_classifier_agent import EmailClassificationAgent


CAPITAL_CALL_TEXT = """Munich, 20/02/2026
ALPINE GROWTH PARTNERS FUND I
CAPITAL CALL NOTICE - CLOSING #1 (05/03/2026)
Dear Investor,
This notice serves to inform you of the following capital call in relation
to your commitment to Alpine Growth Partners Fund I.
Investor Information
Investor Name: Anna Keller
Share Class: A
Currency: EUR
Total Commitment: 750,000.00 EUR
Capital Position
Capital called with this notice: 150,000.00 EUR
Capital remaining after call: 600000.00 EUR
Capital Call Details
Fund-level amount called: 10,000,000.00 EUR
Investor-level amount called: 150,000.00 EUR
Fees and equalization interest included
Total Amount Due: 305,400.00 EUR
Payment Instructions
Value date: 05/03/2026
Beneficiary Bank: Global Custody Bank AG
BIC: CUSTDEFFXXX
IBAN: DE44 5001 0517 5407 3249 31
Reference: Capital Call AGP Fund I
This document is fictitious and provided solely for demonstration
and illustrative purposes. It does not constitute investment advice.
"""


SOPHIE_CAPITAL_CALL_TEXT = """Munich, 20/02/2026
ALPINE GROWTH PARTNERS FUND I
CAPITAL CALL NOTICE – CLOSING #5 (05/03/2026)
Dear Investor,
This notice serves to inform you of the following capital call in relation
to your commitment to Alpine Growth Partners Fund I.
Investor Information
Investor Name: Sophie Laurent
Share Class: A
Currency: EUR
Total Commitment: 1,100,000.00 EUR
Capital Position
Capital called with this notice: 220,000.00 EUR
Capital remaining after call: 880000.00 EUR
Capital Call Details
Fund-level amount called: 10,000,000.00 EUR
Investor-level amount called: 220,000.00 EUR
Fees and equalization interest included
Total Amount Due: 451,600.00 EUR
Payment Instructions
Value date: 05/03/2026
Beneficiary Bank: Global Custody Bank AG
BIC: CUSTDEFFXXX
IBAN: DE44 5001 0517 5407 3249 31
Reference: Capital Call AGP Fund I
This document is fictitious and provided solely for demonstration
and illustrative purposes. It does not constitute investment advice
"""


def create_agent_without_azure() -> EmailClassificationAgent:
    return EmailClassificationAgent.__new__(EmailClassificationAgent)


def test_deterministic_capital_call_extraction_from_clear_labels():
    agent = create_agent_without_azure()
    attachment = {
        "name": "Fictitious_PE_Capital_Call_1.pdf",
        "extracted_content": {"full_text": CAPITAL_CALL_TEXT},
    }

    event = agent._extract_deterministic_document_event(attachment, "Capital Call")

    assert event["document_name"] == "Fictitious_PE_Capital_Call_1.pdf"
    assert event["category"] == "Capital Call"
    assert event["fund_name"] == "Alpine Growth Partners Fund I"
    assert event["pe_company"] == "Alpine Growth Partners"
    assert event["investor"] == "Anna Keller"
    assert event["share_class"] == "A"
    assert event["currency"] == "EUR"
    assert event["total_commitment"] == "750000.00 EUR"
    assert event["capital_called_with_notice"] == "150000.00 EUR"
    assert event["fund_level_amount_called"] == "10000000.00 EUR"
    assert event["investor_level_amount_called"] == "150000.00 EUR"
    assert event["total_amount_due"] == "305400.00 EUR"
    assert event["amount"] == "305400.00 EUR"
    assert event["notice_date"] == "2026-02-20"
    assert event["closing_date"] == "2026-03-05"
    assert event["value_date"] == "2026-03-05"
    assert event["due_date"] == "2026-03-05"
    assert event["reference"] == "Capital Call AGP Fund I"


def test_merge_prefers_deterministic_labels_over_empty_llm_values():
    agent = create_agent_without_azure()
    deterministic = {
        "document_name": "Fictitious_PE_Capital_Call_1.pdf",
        "category": "Capital Call",
        "fund_name": "Alpine Growth Partners Fund I",
        "pe_company": "Alpine Growth Partners",
        "investor": "Anna Keller",
        "amount": "305400.00 EUR",
        "due_date": "2026-03-05",
        "confidence": 0.75,
    }
    llm = {
        "document_name": "Fictitious_PE_Capital_Call_1.pdf",
        "category": "Capital Call",
        "fund_name": None,
        "pe_company": None,
        "investor": None,
        "amount": None,
        "due_date": None,
        "confidence": 0.5,
    }

    event = agent._merge_document_events(deterministic, llm)

    assert event["fund_name"] == "Alpine Growth Partners Fund I"
    assert event["pe_company"] == "Alpine Growth Partners"
    assert event["investor"] == "Anna Keller"
    assert event["amount"] == "305400.00 EUR"
    assert event["due_date"] == "2026-03-05"
    assert "validation_errors" not in event


def test_validation_flags_weak_capital_call_extraction():
    agent = create_agent_without_azure()

    errors = agent._validate_document_event({"category": "Capital Call"})

    assert errors == ["missing_fund_name", "missing_investor", "missing_amount", "missing_due_date"]


def test_exact_sophie_capital_call_is_extracted_locally():
    agent = create_agent_without_azure()
    attachment = {
        "name": "Fictitious_PE_Capital_Call_5.pdf",
        "extracted_content": {"full_text": SOPHIE_CAPITAL_CALL_TEXT},
    }

    event = agent._extract_deterministic_document_event(attachment, "Capital Call")

    assert event["document_name"] == "Fictitious_PE_Capital_Call_5.pdf"
    assert event["category"] == "Capital Call"
    assert event["fund_name"] == "Alpine Growth Partners Fund I"
    assert event["pe_company"] == "Alpine Growth Partners"
    assert event["investor"] == "Sophie Laurent"
    assert event["amount"] == "451600.00 EUR"
    assert event["due_date"] == "2026-03-05"
    assert event["total_commitment"] == "1100000.00 EUR"
    assert event["capital_called_with_notice"] == "220000.00 EUR"
    assert event["investor_level_amount_called"] == "220000.00 EUR"
    assert event["total_amount_due"] == "451600.00 EUR"
    assert event["closing_date"] == "2026-03-05"
    assert event["value_date"] == "2026-03-05"
    assert event["reference"] == "Capital Call AGP Fund I"
    assert agent._is_deterministic_event_complete(event)


async def test_exact_sophie_capital_call_full_event_path_skips_llm():
    agent = create_agent_without_azure()
    attachment = {
        "name": "Fictitious_PE_Capital_Call_5.pdf",
        "extracted_content": {"content": SOPHIE_CAPITAL_CALL_TEXT},
    }

    event = await agent._extract_single_document_event(attachment, "Capital Call")

    assert event["fund_name"] == "Alpine Growth Partners Fund I"
    assert event["pe_company"] == "Alpine Growth Partners"
    assert event["investor"] == "Sophie Laurent"
    assert event["amount"] == "451600.00 EUR"
    assert event["due_date"] == "2026-03-05"
    assert event["extraction_method"] == "deterministic_labels"
    assert "validation_errors" not in event


def test_merge_drops_ungrounded_llm_hallucinations():
    """Regression for the Anna Keller incident.

    The deterministic regex extracts the truth (Anna Keller / EUR / 305400 EUR),
    while the LLM hallucinates Zava + USD + a stale 2023 date. After merge, the
    persisted event must reflect the source document, not the hallucination.
    """
    agent = create_agent_without_azure()
    deterministic = agent._extract_deterministic_document_event(
        {"name": "Anna_Keller_Capital_Call.pdf",
         "extracted_content": {"full_text": CAPITAL_CALL_TEXT}},
        "Capital Call",
    )
    # LLM hallucinated nearly everything, including Zava as the investor.
    hallucinated_llm = {
        "document_name": "Anna_Keller_Capital_Call.pdf",
        "category": "Capital Call",
        "fund_name": "Fictitious Fund I",
        "pe_company": "Fictitious PE",
        "investor": "Zava Private Bank",
        "currency": "USD",
        "amount": "150000 USD",
        "due_date": "2023-11-15",
        "confidence": 0.4,
    }

    event = agent._merge_document_events(deterministic, hallucinated_llm)

    # Deterministic (grounded) values must win.
    assert event["investor"] == "Anna Keller"
    assert event["fund_name"] == "Alpine Growth Partners Fund I"
    assert event["currency"] == "EUR"
    assert event["amount"] == "305400.00 EUR"
    assert event["due_date"] == "2026-03-05"
    # The merged event is fully grounded → no validation errors expected.
    assert "validation_errors" not in event
    # Internal grounding marker must not leak to persistence.
    assert "_source_text" not in event


def test_merge_drops_ungrounded_llm_value_when_deterministic_is_silent():
    """If the deterministic extractor finds nothing for a field but the LLM
    invents a value that does not appear in the source, the value is dropped
    and a `validation_errors` entry is recorded."""
    agent = create_agent_without_azure()
    minimal_text = "Some unrelated text without any of the labels."
    deterministic = {
        "document_name": "doc.pdf",
        "category": "Capital Call",
        "_source_text": minimal_text,
    }
    hallucinated_llm = {
        "investor": "Zava Private Bank",   # not in source
        "currency": "USD",                  # not in source
        "fund_name": "Fictitious Fund I",   # not in source
    }

    event = agent._merge_document_events(deterministic, hallucinated_llm)

    assert event["investor"] is None
    assert event["currency"] is None
    assert event["fund_name"] is None
    errors = event.get("validation_errors", [])
    assert "ungrounded_investor" in errors
    assert "ungrounded_currency" in errors
    assert "ungrounded_fund_name" in errors
