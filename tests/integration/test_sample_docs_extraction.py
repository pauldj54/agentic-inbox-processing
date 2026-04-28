"""End-to-end extraction test against real sample PDFs.

Runs Azure Document Intelligence on every PDF in `sample-docs/`, then runs the
deterministic extractor and prints/asserts the extracted fields. Use this as
the quality gate before shipping any change to the extractor.

Run with:
    python -m tests.integration.test_sample_docs_extraction
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Allow running as a script.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load .env01 for endpoint config.
ENV_FILE = ROOT / ".env01"
if ENV_FILE.exists():
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

from src.agents.email_classifier_agent import EmailClassificationAgent  # noqa: E402
from src.agents.tools.document_intelligence_tool import DocumentIntelligenceTool  # noqa: E402

SAMPLE_DIR = ROOT / "sample-docs"

# Expected ground-truth values per file. None means we did not specify, so the
# extractor may produce anything (still printed for inspection).
EXPECTED = {
    # Capital Calls.
    "Fictitious_PE_Capital_Call_1.pdf": {
        "category": "Capital Call",
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Anna Keller",
        "currency": "EUR",
        "share_class": "A",
        "amount_digits": "30540000",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Capital_Call_3.pdf": {
        "category": "Capital Call",
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Marie Dubois",
        "amount_digits": "20530000",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Capital_Call_4.pdf": {
        "category": "Capital Call",
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Jonas Weber",
        "amount_digits": "36890000",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Capital_Call_5.pdf": {
        "category": "Capital Call",
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Sophie Laurent",
        "amount_digits": "45160000",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Capital_Call_6.pdf": {
        "category": "Capital Call",
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Matteo Rossi",
        "amount_digits": "26620000",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Capital_Call_7.pdf": {
        "category": "Capital Call",
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Isabelle Martin",
        "amount_digits": "32810000",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Capital_Call_8.pdf": {
        "category": "Capital Call",
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Thomas Müller",
        "amount_digits": "61590000",
        "due_date": "2026-03-05",
    },
    # Redistribution Notices.
    "Fictitious_PE_Redistribution_Notice_1.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Paul Meyer",
        "currency": "EUR",
        "amount_digits": "14643192",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Redistribution_Notice_2.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Anna Keller",
        "currency": "EUR",
        "amount_digits": "17831951",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Redistribution_Notice_3.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Anna Keller",
        "currency": "EUR",
        "amount_digits": "11207870",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Redistribution_Notice_4.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Marie Dubois",
        "currency": "EUR",
        "amount_digits": "14768465",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Redistribution_Notice_5.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Anna Keller",
        "currency": "EUR",
        "amount_digits": "11520640",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Redistribution_Notice_6.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Anna Keller",
        "currency": "EUR",
        "amount_digits": "14606185",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Redistribution_Notice_7.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Anna Keller",
        "currency": "EUR",
        "amount_digits": "17157478",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Redistribution_Notice_8.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Anna Keller",
        "currency": "EUR",
        "amount_digits": "12770879",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Redistribution_Notice_9.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Sophie Laurent",
        "currency": "EUR",
        "amount_digits": "6523214",
        "due_date": "2026-03-05",
    },
    # Tax Statements.
    "Fictitious_PE_Tax_Statement_1.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Lukas Schneider",
        "currency": "EUR",
        "amount_digits": "26558928",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Tax_Statement_2.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Anna Keller",
        "currency": "EUR",
        "amount_digits": "21897197",
        "due_date": "2026-03-05",
    },
    "Fictitious_PE_Tax_Statement_3.pdf": {
        "fund_name_substring": "Alpine Growth Partners Fund I",
        "investor": "Marie Dubois",
        "currency": "EUR",
        "amount_digits": "10459612",
        "due_date": "2026-03-05",
    },
}


class _ExtractorOnly(EmailClassificationAgent):
    """Subclass that skips network init so we can call extraction methods directly."""

    def __init__(self):  # noqa: D401 - intentional override
        # Minimal init: do not call super().__init__ which sets up Foundry/Cosmos clients.
        pass


def _build_attachment(name: str, di_result: dict) -> dict:
    return {
        "name": name,
        "extracted_content": {
            "page_count": di_result.get("page_count", 0),
            "full_text": di_result.get("content", ""),
            "tables": di_result.get("tables", []),
        },
    }


async def main() -> int:
    di_tool = DocumentIntelligenceTool()
    extractor = _ExtractorOnly()

    pdfs = sorted(SAMPLE_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {SAMPLE_DIR}")
        return 1

    overall_pass = True
    summary: list[str] = []

    for pdf in pdfs:
        print("=" * 80)
        print(f"FILE: {pdf.name}")
        di_result = await di_tool.analyze_document_from_bytes(pdf.read_bytes(), pdf.name)
        if not di_result.get("success"):
            print(f"  DI FAILED: {di_result.get('error')}")
            summary.append(f"{pdf.name}: DI ERROR")
            overall_pass = False
            continue

        text = di_result.get("content", "")
        print("--- DI text (first 1500 chars) ---")
        print(text[:1500])
        print("--- end DI text ---")

        attachment = _build_attachment(pdf.name, di_result)
        event = extractor._extract_deterministic_document_event(attachment, "Unknown")
        # Drop internal field for printing.
        printable = {k: v for k, v in event.items() if k != "_source_text"}
        print("Extracted event:")
        print(json.dumps(printable, indent=2, default=str))

        expected = EXPECTED.get(pdf.name)
        if expected:
            failures = []
            if expected.get("category") and event.get("category") != expected["category"]:
                failures.append(f"category={event.get('category')!r} expected {expected['category']!r}")
            sub = expected.get("fund_name_substring")
            if sub:
                fund = (event.get("fund_name") or "").lower()
                if sub.lower() not in fund:
                    failures.append(f"fund_name={event.get('fund_name')!r} missing substring {sub!r}")
            if expected.get("investor") and event.get("investor") != expected["investor"]:
                failures.append(f"investor={event.get('investor')!r} expected {expected['investor']!r}")
            if expected.get("currency") and event.get("currency") != expected["currency"]:
                failures.append(f"currency={event.get('currency')!r} expected {expected['currency']!r}")
            if expected.get("share_class") and event.get("share_class") != expected["share_class"]:
                failures.append(f"share_class={event.get('share_class')!r} expected {expected['share_class']!r}")
            if expected.get("amount_digits"):
                amt = "".join(ch for ch in str(event.get("amount") or "") if ch.isdigit())
                if expected["amount_digits"] not in amt:
                    failures.append(f"amount={event.get('amount')!r} digits {amt!r} expected to contain {expected['amount_digits']!r}")
            if expected.get("due_date") and event.get("due_date") != expected["due_date"]:
                failures.append(f"due_date={event.get('due_date')!r} expected {expected['due_date']!r}")

            if failures:
                overall_pass = False
                print(f"  FAIL ({len(failures)}):")
                for f in failures:
                    print(f"    - {f}")
                summary.append(f"{pdf.name}: FAIL ({len(failures)})")
            else:
                print("  PASS")
                summary.append(f"{pdf.name}: PASS")
        else:
            summary.append(f"{pdf.name}: (no expectations)")

    print("=" * 80)
    print("SUMMARY")
    for line in summary:
        print(f"  {line}")
    print("=" * 80)
    return 0 if overall_pass else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
