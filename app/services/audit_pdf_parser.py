"""Layout-aware PDF text extraction for policy audit (pdfplumber + PyMuPDF fallback)."""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import MAX_TEXT_LENGTH
from app.services.document_extraction import extract_pdf_text, truncate_text

logger = logging.getLogger(__name__)


def _table_to_lines(table: list[list]) -> list[str]:
    lines: list[str] = []
    for row in table:
        if not row:
            continue
        cells = [str(c or "").strip() for c in row]
        if any(cells):
            lines.append("| " + " | ".join(cells) + " |")
    return lines


def extract_audit_pdf_text(file_path: Path) -> str:
    """Extract text and tables from a policy PDF for metric extraction."""
    if file_path.suffix.lower() != ".pdf":
        return ""

    parts: list[str] = []
    try:
        import pdfplumber

        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_text = (page.extract_text() or "").strip()
                if page_text:
                    parts.append(f"--- Page {page_num} ---\n{page_text}")

                try:
                    tables = page.extract_tables() or []
                except Exception:
                    tables = []
                for table in tables:
                    table_lines = _table_to_lines(table)
                    if table_lines:
                        parts.append(
                            f"--- Page {page_num} table ---\n"
                            + "\n".join(table_lines)
                        )
    except ImportError:
        logger.warning("pdfplumber not installed; falling back to PyMuPDF")
    except Exception as exc:
        logger.warning("pdfplumber extraction failed for %s: %s", file_path.name, exc)

    result = "\n\n".join(parts).strip()
    if len(result) >= 100:
        return truncate_text(result)

    fallback = extract_pdf_text(file_path)
    if fallback.strip():
        logger.info("Using PyMuPDF fallback for audit PDF: %s", file_path.name)
        return truncate_text(fallback)

    return ""
