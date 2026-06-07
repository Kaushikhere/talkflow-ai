"""Filter low-quality chunks before embedding into Chroma."""
from __future__ import annotations

import re

from app.config import KB_CHUNK_FILTER_ENABLED, KB_CHUNK_MIN_CHARS

_PAGE_ONLY = re.compile(r"^(?:page\s+)?\d+(?:\s+of\s+\d+)?$", re.I)
_BOILERPLATE_ONLY = re.compile(
    r"^(?:www\.careinsurance\.com|careinsurance\.com|prospectus\s+cum\s+sales\s+literature)$",
    re.I,
)
_UIN_ONLY = re.compile(r"^UIN:\s*[\w\d\-]+$", re.I)


def strip_title_prefix(chunk: str, doc_title: str) -> str:
    prefix = f"Document: {doc_title}\n\n"
    if chunk.startswith(prefix):
        return chunk[len(prefix) :]
    return chunk


def _alpha_ratio(text: str) -> float:
    if not text:
        return 0.0
    letters = sum(1 for c in text if c.isalpha())
    return letters / len(text)


def is_quality_chunk(body: str, *, min_chars: int | None = None) -> bool:
    if not KB_CHUNK_FILTER_ENABLED:
        return True

    minimum = min_chars if min_chars is not None else KB_CHUNK_MIN_CHARS
    text = body.strip()
    if len(text) < minimum:
        return False

    collapsed = re.sub(r"\s+", " ", text)
    if _PAGE_ONLY.match(collapsed):
        return False
    if _BOILERPLATE_ONLY.match(collapsed):
        return False
    if _UIN_ONLY.match(collapsed):
        return False

    if _alpha_ratio(text) < 0.4:
        return False

    if "www.careinsurance.com" in text.lower() and len(text) < 120:
        return False

    return True
