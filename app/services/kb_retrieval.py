import re

from app.config import KB_RERANK_ENABLED, KB_RERANK_POOL, KB_RETRIEVE_POOL, KB_TOP_K
from app.database import get_kb_document_by_id, list_indexed_kb_documents
from app.services.kb_embeddings import query_chunks, query_chunks_for_document
from app.services.kb_rerank import rerank_hits

MAX_KB_CONTEXT_CHARS = 24_000

_QUERY_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "what",
        "which",
        "who",
        "how",
        "when",
        "where",
        "why",
        "tell",
        "me",
        "about",
        "please",
        "can",
        "you",
        "do",
        "does",
        "of",
        "for",
        "to",
        "in",
        "on",
        "and",
        "or",
        "with",
        "from",
        "this",
        "that",
        "it",
        "be",
        "explain",
        "describe",
        "give",
        "show",
        "list",
        "summarize",
        "summary",
        "overview",
        "main",
        "key",
        "details",
    }
)

_TITLE_MATCH_DISTANCE_FACTOR = 0.72
_STRONG_TITLE_RATIO = 0.5
_FOCUS_TITLE_RATIO = 0.67

_doc_cache: list[dict] | None = None
_title_token_index: dict[str, set[int]] | None = None


def refresh_kb_document_cache() -> None:
    """Reload title index after ingest (call at startup and after pipeline)."""
    global _doc_cache, _title_token_index
    docs = list_indexed_kb_documents()
    _doc_cache = docs
    index: dict[str, set[int]] = {}
    for doc_index, doc in enumerate(docs):
        title = (doc.get("title") or "").lower()
        for token in re.findall(r"[a-z0-9]+", title):
            if len(token) >= 2:
                index.setdefault(token, set()).add(doc_index)
    _title_token_index = index


def _ensure_doc_cache() -> None:
    if _doc_cache is None or _title_token_index is None:
        refresh_kb_document_cache()


def _query_tokens(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", query.lower())
    return [w for w in words if len(w) >= 2 and w not in _QUERY_STOP_WORDS]


def _query_phrases(query: str) -> list[str]:
    """Multi-word product names, e.g. 'secure plus' from 'What is Secure Plus?'."""
    words = re.findall(r"[a-z0-9]+", query.lower())
    phrases: list[str] = []
    for i in range(len(words) - 1):
        a, b = words[i], words[i + 1]
        if a in _QUERY_STOP_WORDS or b in _QUERY_STOP_WORDS:
            continue
        if len(a) >= 2 and len(b) >= 2:
            phrases.append(f"{a} {b}")
    for i in range(len(words) - 2):
        a, b, c = words[i], words[i + 1], words[i + 2]
        if any(w in _QUERY_STOP_WORDS for w in (a, b, c)):
            continue
        phrases.append(f"{a} {b} {c}")
    return phrases


def _title_match_ratio(title: str, tokens: list[str], phrases: list[str]) -> float:
    title_lower = title.lower()
    if phrases:
        for phrase in phrases:
            if phrase in title_lower:
                return 1.0
    if not tokens:
        return 0.0
    matched = sum(1 for token in tokens if token in title_lower)
    if matched == 0:
        return 0.0
    if matched >= 2:
        return matched / len(tokens)
    if len(tokens) == 1 and matched == 1:
        return 1.0
    return 0.0


def _documents_matching_query_title(query: str) -> list[dict]:
    tokens = _query_tokens(query)
    phrases = _query_phrases(query)
    if not tokens and not phrases:
        return []

    _ensure_doc_cache()
    assert _doc_cache is not None
    assert _title_token_index is not None

    if phrases:
        candidate_indexes: set[int] = set()
        for doc_index, doc in enumerate(_doc_cache):
            title_lower = (doc.get("title") or "").lower()
            if any(p in title_lower for p in phrases):
                candidate_indexes.add(doc_index)
    elif len(tokens) == 1:
        candidate_indexes = set(_title_token_index.get(tokens[0], set()))
    else:
        sets = [_title_token_index.get(token, set()) for token in tokens]
        candidate_indexes = set.intersection(*sets) if all(sets) else set()
        if not candidate_indexes:
            candidate_indexes = set().union(*sets)

    seen_ids: set[int] = set()
    matches: list[dict] = []
    for doc_index in candidate_indexes:
        doc = _doc_cache[doc_index]
        doc_id = doc.get("id")
        if doc_id in seen_ids:
            continue
        seen_ids.add(doc_id)
        title = doc.get("title") or ""
        ratio = _title_match_ratio(title, tokens, phrases)
        if ratio >= _STRONG_TITLE_RATIO:
            matches.append({**doc, "title_match_ratio": ratio})
    matches.sort(key=lambda d: d["title_match_ratio"], reverse=True)
    return matches


def _dedupe_hits(hits: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for hit in hits:
        chunk_id = hit.get("id")
        if chunk_id and chunk_id in seen:
            continue
        if chunk_id:
            seen.add(chunk_id)
        unique.append(hit)
    return sorted(unique, key=lambda h: h.get("distance", 999))


def _hits_from_documents(
    docs: list[dict],
    query: str,
    *,
    total_k: int,
    per_doc_k: int,
) -> list[dict]:
    hits: list[dict] = []
    for doc in docs:
        if len(hits) >= total_k:
            break
        doc_id = doc["id"]
        need = min(per_doc_k, total_k - len(hits))
        per_doc = query_chunks_for_document(doc_id, query, top_k=need)
        boost = _TITLE_MATCH_DISTANCE_FACTOR * (
            1.0 - 0.1 * (1.0 - doc.get("title_match_ratio", 1.0))
        )
        for hit in per_doc:
            hit["distance"] = (hit.get("distance") or 1.0) * boost
            hit["title_boosted"] = True
        hits.extend(per_doc)
    return _dedupe_hits(hits)[:total_k]


def retrieve_kb_hits(query: str, *, top_k: int | None = None) -> list[dict]:
    k = top_k or KB_TOP_K
    title_docs = _documents_matching_query_title(query)

    if title_docs:
        primary_ratio = title_docs[0].get("title_match_ratio", 0)
        if primary_ratio >= _FOCUS_TITLE_RATIO:
            per_doc = max(20, k)
            focused = _hits_from_documents(
                title_docs[:2],
                query,
                total_k=k,
                per_doc_k=per_doc,
            )
            if len(focused) >= min(4, min(k, KB_TOP_K)):
                return focused

        priority_slots = min(k, max(6, k - 2))
        priority_hits = _hits_from_documents(
            title_docs[:2],
            query,
            total_k=priority_slots,
            per_doc_k=max(6, priority_slots),
        )
        remaining = k - len(priority_hits)
        if remaining <= 0:
            return priority_hits[:k]

        used_doc_ids = {
            (h.get("metadata") or {}).get("document_id") for h in priority_hits
        }
        pool_size = max(remaining * 4, 16)
        vector_hits = query_chunks(query, top_k=pool_size)
        filler: list[dict] = []
        seen_ids = {h.get("id") for h in priority_hits if h.get("id")}
        for hit in vector_hits:
            chunk_id = hit.get("id")
            doc_id = (hit.get("metadata") or {}).get("document_id")
            if chunk_id in seen_ids:
                continue
            if doc_id in used_doc_ids:
                continue
            seen_ids.add(chunk_id)
            filler.append(hit)
            if len(filler) >= remaining:
                break
        return _dedupe_hits(priority_hits + filler)[:k]

    pool_size = max(k * 4, 20)
    vector_hits = query_chunks(query, top_k=pool_size)
    return _dedupe_hits(vector_hits)[:k]


def _retrieval_pool_size(final_k: int) -> int:
    if KB_RERANK_ENABLED:
        return max(final_k, KB_RETRIEVE_POOL)
    return final_k


def _rank_candidates(query: str, candidates: list[dict]) -> list[dict]:
    """Rerank only the top KB_RERANK_POOL candidates (cross-encoder is the main latency cost)."""
    if not KB_RERANK_ENABLED or not candidates:
        return candidates
    cap = min(len(candidates), KB_RERANK_POOL)
    if cap <= 0:
        return candidates
    head = candidates[:cap]
    tail = candidates[cap:]
    ranked_head = rerank_hits(query, head, top_k=len(head))
    return ranked_head + tail


def _title_from_chunk_text(text: str) -> str | None:
    if not text.startswith("Document: "):
        return None
    first_line = text.split("\n", 1)[0]
    title = first_line[len("Document: ") :].strip()
    return title or None


def _strip_document_prefix(text: str) -> str:
    """Remove ingested filename/title prefix so the LLM does not echo raw catalog names."""
    if not text.startswith("Document: "):
        return text
    parts = text.split("\n\n", 1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip()
    return text


def _resolve_hit_title(hit: dict, title_cache: dict[int, str]) -> str:
    meta = hit.get("metadata") or {}
    title = (meta.get("title") or "").strip()
    if title:
        return title

    doc_id = meta.get("document_id")
    if doc_id is not None:
        try:
            doc_id_int = int(doc_id)
        except (TypeError, ValueError):
            doc_id_int = None
        if doc_id_int is not None:
            if doc_id_int in title_cache:
                return title_cache[doc_id_int]
            doc = get_kb_document_by_id(doc_id_int)
            if doc and (doc.get("title") or "").strip():
                resolved = doc["title"].strip()
                title_cache[doc_id_int] = resolved
                return resolved

    parsed = _title_from_chunk_text(hit.get("text") or "")
    if parsed:
        return parsed
    return "Document"


_HASH_SUFFIX_RE = re.compile(r"\s+[0-9a-f]{6,}\s*$", re.IGNORECASE)
_NOISE_PHRASES = (
    " prospectus cum sales literature",
    " policy terms conditions",
    " policy terms and conditions",
    " brochure",
    " prospectus",
    " health insurance product",
    " travel insurance product",
    " personal accident product",
    " insurance product",
)


def _clean_source_title(raw: str) -> str:
    """Return a customer-facing product name by stripping internal catalog noise."""
    title = raw.strip()
    # remove trailing hex hashes like "3d0d593145"
    title = _HASH_SUFFIX_RE.sub("", title).strip()
    # remove parenthetical type labels: "(health insurance product)"
    title = re.sub(r"\s*\([^)]*product[^)]*\)", "", title, flags=re.IGNORECASE).strip()
    # remove known noise suffixes (longest first to avoid partial matches)
    lower = title.lower()
    for phrase in _NOISE_PHRASES:
        if lower.endswith(phrase):
            title = title[: len(title) - len(phrase)].strip()
            lower = title.lower()
    # strip leading "add on " prefix
    if lower.startswith("add on "):
        title = title[7:].strip()
        lower = title.lower()
    # title-case the result
    return title.title() if title else raw.strip()


def _build_display_sources(hits: list[dict]) -> list[dict]:
    """Group retrieved chunks by cleaned product title (one row per document/product).

    The retrieval pool can return many chunks from the same brochure on different
    pages — the UI shows one entry per product with all referenced pages combined.
    """
    title_cache: dict[int, str] = {}
    seen_chunk: set[tuple[int | None, int | None]] = set()
    grouped: dict[str, dict] = {}
    order: list[str] = []

    for hit in hits:
        meta = hit.get("metadata") or {}
        doc_id = meta.get("document_id")
        page_number = meta.get("page_number")
        try:
            doc_key = int(doc_id) if doc_id is not None else None
        except (TypeError, ValueError):
            doc_key = None

        chunk_key = (doc_key, page_number)
        if chunk_key in seen_chunk:
            continue
        seen_chunk.add(chunk_key)

        title = _clean_source_title(_resolve_hit_title(hit, title_cache))
        group_key = title.lower()
        if group_key not in grouped:
            grouped[group_key] = {
                "title": title,
                "pages": set(),
                "document_id": doc_key,
                "distance": hit.get("distance"),
                "snippet": (hit.get("text") or "")[:200],
            }
            if "rerank_score" in hit:
                grouped[group_key]["rerank_score"] = hit["rerank_score"]
            order.append(group_key)

        if page_number is not None:
            grouped[group_key]["pages"].add(page_number)

    sources: list[dict] = []
    for index, group_key in enumerate(order, start=1):
        entry = grouped[group_key]
        pages = sorted(entry["pages"])
        doc_key = entry["document_id"]
        summary: dict = {
            "index": index,
            "title": entry["title"],
            "pages": pages,
            "page_number": pages[0] if len(pages) == 1 else None,
            "document_id": doc_key,
            "distance": entry.get("distance"),
            "snippet": entry.get("snippet"),
        }
        if doc_key is not None:
            first_page = pages[0] if pages else None
            page_q = f"?page={first_page}" if first_page is not None else ""
            summary["view_url"] = f"/kb/documents/{doc_key}/file{page_q}"
        if "rerank_score" in entry:
            summary["rerank_score"] = entry["rerank_score"]
        sources.append(summary)

    return sources


_CATALOG_QUERY_RE = re.compile(
    r"\b("
    r"what\s+plans?|which\s+plans?|list\s+(all\s+)?(plans?|products?)|"
    r"how\s+many\s+(plans?|products?)|all\s+(plans?|products?)|"
    r"plans?\s+do\s+you\s+(have|offer)|products?\s+do\s+you\s+(have|offer)|"
    r"what\s+(plans?|products?)\s+(are\s+)?available"
    r")\b",
    re.IGNORECASE,
)


def _is_catalog_query(query: str) -> bool:
    return bool(_CATALOG_QUERY_RE.search(query))


def _all_catalog_product_names() -> list[str]:
    """All indexed KB product names (cleaned), for catalog-style questions."""
    _ensure_doc_cache()
    assert _doc_cache is not None
    seen: set[str] = set()
    names: list[str] = []
    for doc in _doc_cache:
        clean = _clean_source_title(doc.get("title") or "")
        lower = clean.lower()
        if lower not in seen and clean and lower != "document":
            seen.add(lower)
            names.append(clean)
    return names


def _extract_product_names(hits: list[dict]) -> list[str]:
    """Unique cleaned product names from retrieved hits, in retrieval order."""
    title_cache: dict[int, str] = {}
    seen: set[str] = set()
    names: list[str] = []
    for hit in hits:
        raw = _resolve_hit_title(hit, title_cache)
        clean = _clean_source_title(raw)
        lower = clean.lower()
        if lower not in seen and clean and clean.lower() != "document":
            seen.add(lower)
            names.append(clean)
    return names


def build_kb_context(query: str, *, top_k: int | None = None) -> tuple[str, list[dict], list[str]]:
    k = top_k or KB_TOP_K
    pool = _retrieval_pool_size(k)
    candidates = retrieve_kb_hits(query, top_k=pool)
    ranked_pool = _rank_candidates(query, candidates)
    hits = ranked_pool[:k]
    if not hits:
        return "", [], []

    product_names = _extract_product_names(ranked_pool)
    if _is_catalog_query(query):
        seen = {n.lower() for n in product_names}
        for name in _all_catalog_product_names():
            if name.lower() not in seen:
                product_names.append(name)
                seen.add(name.lower())

    parts: list[str] = []
    total = 0

    for index, hit in enumerate(hits, start=1):
        header = f"\n--- Excerpt {index} ---\n"
        body = _strip_document_prefix((hit.get("text") or "").strip())
        block = header + body + "\n"

        if total + len(block) > MAX_KB_CONTEXT_CHARS:
            remaining = MAX_KB_CONTEXT_CHARS - total - len(header) - 50
            if remaining > 200:
                block = header + body[:remaining] + "\n[Excerpt truncated...]\n"
                parts.append(block)
            parts.append("\n[Additional KB excerpts omitted due to length...]\n")
            break

        parts.append(block)
        total += len(block)

    parts.append("\n--- End of excerpts ---\n")
    sources = _build_display_sources(ranked_pool)
    return "".join(parts), sources, product_names
