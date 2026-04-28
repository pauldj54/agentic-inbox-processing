"""Unit tests for compact Document Intelligence result shaping."""

from src.agents.tools.document_intelligence_tool import DocumentIntelligenceTool


class FakeCell:
    def __init__(self, row_index: int, column_index: int, content: str):
        self.row_index = row_index
        self.column_index = column_index
        self.content = content


class FakeTable:
    row_count = 2
    column_count = 2
    cells = [
        FakeCell(0, 0, "Investor"),
        FakeCell(0, 1, "Sophie Laurent"),
        FakeCell(1, 0, "Total Amount Due"),
        FakeCell(1, 1, "451,600.00 EUR"),
    ]


class FakeResult:
    content = "Capital Call Notice\nTotal Amount Due: 451,600.00 EUR"
    pages = [object()]
    tables = [FakeTable()]
    paragraphs = [object()]
    key_value_pairs = [object()]


def test_process_result_returns_only_compact_content_and_tables():
    tool = DocumentIntelligenceTool.__new__(DocumentIntelligenceTool)

    result = tool._process_result(FakeResult(), "sample.pdf")

    assert result["success"] is True
    assert result["source"] == "sample.pdf"
    assert result["page_count"] == 1
    assert result["content"] == FakeResult.content
    assert result["table_count"] == 1
    assert result["tables"] == [
        {
            "table_index": 0,
            "row_count": 2,
            "column_count": 2,
            "rows": [
                ["Investor", "Sophie Laurent"],
                ["Total Amount Due", "451,600.00 EUR"],
            ],
        }
    ]
    assert "full_text" not in result
    assert "summary" not in result
    assert "key_value_pairs" not in result
    assert "paragraphs" not in result
