"""Resolve per-metric source excerpts from stored citations or extracted text fallback."""
from __future__ import annotations

import re
from typing import Any

_PAGE_HEADER_RE = re.compile(r"^--- Page (\d+)(?:\s+table)? ---\s*$", re.MULTILINE)

_METRIC_KEYWORDS: dict[str, list[str]] = {
    "ped_waiting_period_months": [
        "pre-existing",
        "pre existing",
        "ped waiting",
        "waiting period",
        "preexisting",
    ],
    "co_payment_percentage": [
        "co-payment",
        "co payment",
        "copay",
        "co-pay",
        "cost sharing",
    ],
    "room_rent_cap": [
        "room rent",
        "room category",
        "sub-limit",
        "sub limit",
        "sub-limits",
        "accommodation",
    ],
    "restoration_benefit": [
        "restoration",
        "reinstatement",
        "restore",
        "sum insured",
    ],
}

_RISK_KEYWORDS = ["risk", "limit", "waiting", "co-pay", "co payment", "exclusion"]
_STRENGTH_KEYWORDS = ["benefit", "cover", "restoration", "no sub", "no cap"]


def _split_pages(extracted_text: str) -> list[tuple[int | None, str]]:
    """Split extracted text into (page_number, content) segments."""
    if not extracted_text.strip():
        return []

    segments: list[tuple[int | None, str]] = []
    current_page: int | None = None
    buffer: list[str] = []

    for line in extracted_text.splitlines():
        match = _PAGE_HEADER_RE.match(line.strip())
        if match:
            if buffer:
                segments.append((current_page, "\n".join(buffer).strip()))
                buffer = []
            current_page = int(match.group(1))
            continue
        buffer.append(line)

    if buffer:
        segments.append((current_page, "\n".join(buffer).strip()))
    return segments


def _best_paragraph(page_content: str, keywords: list[str], value_hint: str | None = None) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", page_content) if p.strip()]
    if not paragraphs:
        paragraphs = [ln.strip() for ln in page_content.splitlines() if ln.strip()]

    scored: list[tuple[int, str]] = []
    value_lower = (value_hint or "").lower()
    for para in paragraphs:
        lower = para.lower()
        score = sum(1 for kw in keywords if kw in lower)
        if value_lower and value_lower in lower:
            score += 3
        if score > 0:
            scored.append((score, para))

    if not scored:
        return ""
    scored.sort(key=lambda x: x[0], reverse=True)
    excerpt = scored[0][1]
    return excerpt[:400]


def _normalize_source_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    excerpt = str(entry.get("excerpt") or "").strip()
    if not excerpt:
        return None
    page = entry.get("page")
    if page is not None:
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = None
    approximate = bool(entry.get("approximate"))
    return {"page": page, "excerpt": excerpt[:400], "approximate": approximate}


def resolve_metric_source(
    metric_key: str,
    value: Any,
    extracted_text: str,
    *,
    stored_sources: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return {page, excerpt, approximate?} for a metric or list item key."""
    if stored_sources and metric_key in stored_sources:
        normalized = _normalize_source_entry(stored_sources[metric_key])
        if normalized:
            return normalized

    keywords = _METRIC_KEYWORDS.get(metric_key, [])
    if metric_key.startswith("risk_"):
        keywords = _RISK_KEYWORDS
    elif metric_key.startswith("strength_"):
        keywords = _STRENGTH_KEYWORDS

    if not keywords and not value:
        return None

    value_hint = str(value) if value is not None else None
    segments = _split_pages(extracted_text)

    best: tuple[int, int | None, str] | None = None
    for page, content in segments:
        if not content:
            continue
        excerpt = _best_paragraph(content, keywords, value_hint)
        if not excerpt:
            continue
        score = sum(1 for kw in keywords if kw in excerpt.lower())
        if value_hint and value_hint.lower() in excerpt.lower():
            score += 2
        if best is None or score > best[0]:
            best = (score, page, excerpt)

    if not best:
        return None

    return {"page": best[1], "excerpt": best[2][:400], "approximate": True}


def build_sources_map(
    policy: dict,
    *,
    stored_sources: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build full sources dict for API response with fallbacks."""
    extracted = policy.get("extracted_text") or ""
    metrics = {
        "room_rent_cap": policy.get("room_rent_cap"),
        "ped_waiting_period_months": policy.get("ped_waiting_period_months"),
        "co_payment_percentage": policy.get("co_payment_percentage"),
        "restoration_benefit": policy.get("restoration_benefit"),
    }

    sources: dict[str, dict[str, Any]] = {}
    for key, value in metrics.items():
        src = resolve_metric_source(key, value, extracted, stored_sources=stored_sources)
        if src:
            sources[key] = src

    meta = meta or {}
    for idx, risk in enumerate(meta.get("key_risks") or []):
        key = f"risk_{idx}"
        src = resolve_metric_source(key, risk, extracted, stored_sources=stored_sources)
        if src:
            sources[key] = src

    for idx, strength in enumerate(meta.get("key_strengths") or []):
        key = f"strength_{idx}"
        src = resolve_metric_source(key, strength, extracted, stored_sources=stored_sources)
        if src:
            sources[key] = src

    if stored_sources:
        for key, entry in stored_sources.items():
            if key not in sources:
                normalized = _normalize_source_entry(entry)
                if normalized:
                    sources[key] = normalized

    return sources
