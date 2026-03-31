"""
Allowed Content Types — Shared security configuration for attachment filtering.

This module is the **single source of truth** for which file types are accepted
by the email ingestion pipeline. It is used by:
  - Link download tool (src/agents/tools/link_download_tool.py)
  - Email classifier agent (src/agents/email_classifier_agent.py)
  - Logic App workflow documentation (logic-apps/email-ingestion/README.md)

The Logic App workflow.json mirrors these rules in its condition expression.
When extending allowed types, update BOTH this file AND workflow.json.

─── HOW TO EXTEND ───────────────────────────────────────────────────────────
To allow additional file types (e.g. CSV, XLSX):

1. Add the MIME type(s) to ALLOWED_CONTENT_TYPES below.
2. Add the extension(s) to ALLOWED_EXTENSIONS below.
3. Update the Logic App workflow.json condition expression in
   "Check_if_allowed_type" to include the new content type.
   Example for CSV + XLSX:
     {
       "or": [
         {"equals": ["@item()['contentType']", "application/pdf"]},
         {"equals": ["@item()['contentType']", "text/csv"]},
         {"equals": ["@item()['contentType']",
           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]}
       ]
     }
4. Re-deploy the Logic App via deploy_updates.ps1.
──────────────────────────────────────────────────────────────────────────────
"""

# MIME types accepted by the ingestion pipeline.
# Attachments and downloaded links with any other content type will be rejected
# and logged in Cosmos DB as a rejected attachment.
ALLOWED_CONTENT_TYPES: set[str] = {
    "application/pdf",
}

# File extensions (lowercase, without dot) accepted by the ingestion pipeline.
# Used as a secondary check when content-type is ambiguous (e.g. octet-stream).
ALLOWED_EXTENSIONS: set[str] = {
    "pdf",
}

# ── Ready-to-use expansion sets (uncomment to enable) ─────────────────────
# CSV:
#   ALLOWED_CONTENT_TYPES.add("text/csv")
#   ALLOWED_EXTENSIONS.add("csv")
#
# XLSX:
#   ALLOWED_CONTENT_TYPES.add(
#       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#   )
#   ALLOWED_EXTENSIONS.add("xlsx")
#
# DOCX:
#   ALLOWED_CONTENT_TYPES.add(
#       "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
#   )
#   ALLOWED_EXTENSIONS.add("docx")


def is_allowed_content_type(content_type: str) -> bool:
    """Check if a MIME content type is in the allowed set.

    Args:
        content_type: MIME type string, e.g. "application/pdf; charset=utf-8"

    Returns:
        True if the base MIME type is allowed.
    """
    if not content_type:
        return False
    base_type = content_type.lower().split(";")[0].strip()
    return base_type in ALLOWED_CONTENT_TYPES


def is_allowed_extension(filename: str) -> bool:
    """Check if a filename has an allowed extension.

    Args:
        filename: File name, e.g. "report.pdf"

    Returns:
        True if the extension is allowed.
    """
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[-1].lower()
    return ext in ALLOWED_EXTENSIONS
