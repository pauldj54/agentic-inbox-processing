"""
Document Intelligence Tool for extracting text and tables from PDFs.
Uses Azure Document Intelligence with the Layout model.
Authentication via DefaultAzureCredential (managed identity, CLI, VS Code).
"""

import os
import logging
from typing import Optional
from datetime import datetime
from azure.identity import DefaultAzureCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

logger = logging.getLogger(__name__)


class DocumentIntelligenceTool:
    """Tool for processing documents with Azure Document Intelligence."""
    
    def __init__(self, endpoint: Optional[str] = None):
        """
        Initialize the Document Intelligence client.
        
        Args:
            endpoint: Document Intelligence endpoint. If not provided, reads from
                     DOCUMENT_INTELLIGENCE_ENDPOINT environment variable.
        """
        self.endpoint = endpoint or os.environ.get("DOCUMENT_INTELLIGENCE_ENDPOINT")
        if not self.endpoint:
            raise ValueError(
                "Document Intelligence endpoint is required. "
                "Set DOCUMENT_INTELLIGENCE_ENDPOINT environment variable."
            )
        
        # Use DefaultAzureCredential for passwordless auth
        self.credential = DefaultAzureCredential()
        self.client = DocumentIntelligenceClient(
            endpoint=self.endpoint,
            credential=self.credential
        )
    
    async def analyze_document_from_url(self, document_url: str) -> dict:
        """
        Analyze a document from a URL using the Layout model.
        
        Args:
            document_url: URL to the PDF document (must be accessible)
            
        Returns:
            Dictionary containing extracted text, tables, and metadata
        """
        logger.info(f"Analyzing document from URL: {document_url[:50]}...")
        
        try:
            # Start the analysis with Layout model
            poller = self.client.begin_analyze_document(
                model_id="prebuilt-layout",
                body=AnalyzeDocumentRequest(url_source=document_url),
            )
            
            # Wait for completion
            result = poller.result()
            
            return self._process_result(result, document_url)
            
        except Exception as e:
            logger.error(f"Error analyzing document: {e}")
            return {
                "success": False,
                "error": str(e),
                "document_url": document_url,
                "analyzed_at": datetime.utcnow().isoformat()
            }
    
    async def analyze_document_from_bytes(self, document_bytes: bytes, filename: str = "document.pdf") -> dict:
        """
        Analyze a document from bytes.
        
        Args:
            document_bytes: Raw bytes of the document
            filename: Name of the document for reference
            
        Returns:
            Dictionary containing extracted text, tables, and metadata
        """
        logger.info(f"Analyzing document from bytes: {filename}")
        
        try:
            # Start the analysis with Layout model
            poller = self.client.begin_analyze_document(
                model_id="prebuilt-layout",
                body=document_bytes,
                content_type="application/pdf",
            )
            
            # Wait for completion
            result = poller.result()
            
            return self._process_result(result, filename)
            
        except Exception as e:
            logger.error(f"Error analyzing document: {e}")
            return {
                "success": False,
                "error": str(e),
                "filename": filename,
                "analyzed_at": datetime.utcnow().isoformat()
            }
    
    def _process_result(self, result, source_reference: str, compact: bool = True) -> dict:
        """
        Process the Document Intelligence analysis result.
        
        Args:
            result: AnalyzeResult from Document Intelligence
            source_reference: Original URL or filename
            compact: If True, return minimal data for classification (default: True)
            
        Returns:
            Structured dictionary with extracted content
        """
        content = result.content if hasattr(result, 'content') else ""
        
        # In compact mode, limit text to first 8000 chars to reduce token usage
        # while still preserving labelled fields that often live below the
        # letterhead/header (investor name, currency, totals, value date, etc.).
        if compact and len(content) > 8000:
            content = content[:8000] + "\n... [text truncated for processing]"
        
        # Extract tables with structure (essential for PE document classification)
        tables = []
        if hasattr(result, 'tables') and result.tables:
            for table_idx, table in enumerate(result.tables[:5]):  # Limit to first 5 tables
                table_data = {
                    "table_index": table_idx,
                    "row_count": table.row_count,
                    "column_count": table.column_count,
                }
                
                # Create row-based representation directly (skip cells for compact mode)
                table_data["rows"] = self._cells_to_rows(
                    [{"row_index": c.row_index, "column_index": c.column_index, "content": c.content}
                     for c in table.cells],
                    min(table.row_count, 15),  # Limit rows for compact mode
                    table.column_count
                )
                tables.append(table_data)
        
        # Document summary
        page_count = len(result.pages) if hasattr(result, 'pages') else 0
        
        # Compact output for classification: content text plus compact table rows only.
        # Do not return layout geometry, spans, paragraphs, or key-value-pair payloads.
        return {
            "success": True,
            "source": source_reference,
            "analyzed_at": datetime.utcnow().isoformat(),
            "page_count": page_count,
            "content": content,
            "tables": tables,
            "table_count": len(tables),
        }
    
    def _cells_to_rows(self, cells: list, row_count: int, column_count: int) -> list:
        """
        Convert flat cell list to row-based 2D array.
        
        Args:
            cells: List of cell dictionaries
            row_count: Number of rows in the table
            column_count: Number of columns in the table
            
        Returns:
            List of rows, where each row is a list of cell contents
        """
        # Initialize empty grid
        grid = [["" for _ in range(column_count)] for _ in range(row_count)]
        
        # Fill in cells
        for cell in cells:
            row = cell["row_index"]
            col = cell["column_index"]
            if row < row_count and col < column_count:
                grid[row][col] = cell["content"]
        
        return grid


# Tool function definition for agent framework
def get_document_intelligence_tool_definition() -> dict:
    """
    Returns the tool definition for the Azure AI Agent framework.
    """
    return {
        "type": "function",
        "function": {
            "name": "process_document",
            "description": (
                "Extracts compact text and table rows from a PDF document using "
                "Azure Document Intelligence. Use this tool to analyze PDF attachments "
                "from emails to understand their content for classification. Returns "
                "only the document content text and compact tables with rows/columns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_url": {
                        "type": "string",
                        "description": "URL to the PDF document to analyze"
                    }
                },
                "required": ["document_url"]
            }
        }
    }
