import re

from app.config import KB_TOP_K
from app.database import list_indexed_kb_documents
from app.services.kb_embeddings import query_chunks, query_chunks_for_document

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
            per_doc = max(8, k)
            focused = _hits_from_documents(
                title_docs[:2],
                query,
                total_k=k,
                per_doc_k=per_doc,
            )
            if len(focused) >= min(4, k):
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


def build_kb_context(query: str, *, top_k: int | None = None) -> tuple[str, list[dict]]:
    k = top_k or KB_TOP_K
    hits = retrieve_kb_hits(query, top_k=k)
    if not hits:
        return "", []

    parts = [
        "Care Health Insurance knowledge base excerpts for this question.\n"
        "Instructions:\n"
        "- Answer fully using the excerpts below (benefits, coverage, waiting periods, "
        "eligibility, limits, optional covers, etc. when present).\n"
        "- Organize the reply with clear headings or bullet points.\n"
        "- Cite document titles and page numbers for major points.\n"
        "- Only state that specific information is missing if it is not in any excerpt; "
        "do not add generic disclaimers that excerpts are incomplete or that more "
        "research is needed when the excerpts already support an answer.\n"
        "- Do not invent policy details not shown in the excerpts.\n",
    ]
    sources: list[dict] = []
    total = len(parts[0])

    for index, hit in enumerate(hits, start=1):
        meta = hit.get("metadata") or {}
        title = meta.get("title") or "Document"
        page = meta.get("page_number")
        page_label = f", page {page}" if page is not None else ""
        header = f"\n--- KB source {index}: {title}{page_label} ---\n"
        body = (hit.get("text") or "").strip()
        block = header + body + "\n"

        if total + len(block) > MAX_KB_CONTEXT_CHARS:
            remaining = MAX_KB_CONTEXT_CHARS - total - len(header) - 50
            if remaining > 200:
                block = header + body[:remaining] + "\n[Excerpt truncated...]\n"
                parts.append(block)
                sources.append(_source_summary(index, hit))
            parts.append("\n[Additional KB excerpts omitted due to length...]\n")
            break

        parts.append(block)
        total += len(block)
        sources.append(_source_summary(index, hit))

    parts.append("\n--- End of knowledge base excerpts ---\n")
    return "".join(parts), sources


def _source_summary(index: int, hit: dict) -> dict:
    meta = hit.get("metadata") or {}
    return {
        "index": index,
        "title": meta.get("title"),
        "page_number": meta.get("page_number"),
        "document_id": meta.get("document_id"),
        "distance": hit.get("distance"),
        "snippet": (hit.get("text") or "")[:200],
    }
