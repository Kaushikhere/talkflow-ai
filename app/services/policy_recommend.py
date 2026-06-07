"""Policy recommendation from KB based on user profile."""
from __future__ import annotations

import json
import logging
import re

from app.config import GROQ_CHAT_MODEL, KB_ENABLED
from app.database import list_indexed_kb_documents
from app.services.groq_client import get_groq_client
from app.services.kb_retrieval import build_kb_context, retrieve_kb_hits

logger = logging.getLogger(__name__)


_PRIORITY_QUERIES: dict[str, str] = {
    "maternity": "maternity coverage waiting period joy plan",
    "critical illness": "critical illness cover assure plan benefits",
    "accident": "personal accident secure plus coverage",
    "senior": "senior citizen health advantage plan",
    "top up": "top up enhance super mediclaim deductible",
    "travel": "travel insurance explore plan",
}


def _candidate_products() -> list[dict]:
    docs = list_indexed_kb_documents()
    seen_titles: set[str] = set()
    candidates: list[dict] = []
    for doc in docs:
        title = (doc.get("title") or "").strip()
        key = title.lower()
        if not title or key in seen_titles:
            continue
        seen_titles.add(key)
        candidates.append(doc)
    return candidates


def _profile_search_queries(
    *,
    age: int,
    budget_monthly: int | None,
    pre_existing: bool,
    family_size: int,
    priorities: list[str],
) -> list[str]:
    queries = [
        f"health insurance plan benefits eligibility age {age} family {family_size}",
    ]
    if pre_existing:
        queries.append("waiting period pre-existing disease coverage")
    if budget_monthly:
        queries.append(f"affordable health plan premium budget {budget_monthly}")
    for priority in priorities:
        key = priority.strip().lower()
        if key in _PRIORITY_QUERIES:
            queries.append(_PRIORITY_QUERIES[key])
        elif key:
            queries.append(f"{priority} insurance plan benefits")
    return queries


def recommend_policies(
    *,
    age: int,
    budget_monthly: int | None = None,
    pre_existing: bool = False,
    family_size: int = 1,
    priorities: list[str] | None = None,
) -> dict:
    if not KB_ENABLED:
        return {"recommendations": [], "error": "Knowledge base is disabled"}

    priorities = priorities or []
    candidates = _candidate_products()
    product_context: list[dict] = []

    for query in _profile_search_queries(
        age=age,
        budget_monthly=budget_monthly,
        pre_existing=pre_existing,
        family_size=family_size,
        priorities=priorities,
    ):
        hits = retrieve_kb_hits(query, top_k=6)
        for hit in hits:
            meta = hit.get("metadata") or {}
            doc_id = meta.get("document_id")
            title = meta.get("title")
            if not doc_id or not title:
                continue
            if any(p["document_id"] == doc_id for p in product_context):
                continue
            product_context.append(
                {
                    "document_id": doc_id,
                    "title": title,
                    "snippet": (hit.get("text") or "")[:400],
                    "page_number": meta.get("page_number"),
                }
            )
            if len(product_context) >= 8:
                break
        if len(product_context) >= 8:
            break

    if not product_context:
        ctx, _, _names = build_kb_context("best health insurance plan benefits", top_k=8)
        excerpt_block = ctx
    else:
        excerpt_block = "\n\n".join(
            f"Product: {p['title']} (doc {p['document_id']}, p.{p.get('page_number')})\n{p['snippet']}"
            for p in product_context
        )

    profile = {
        "age": age,
        "budget_monthly": budget_monthly,
        "pre_existing": pre_existing,
        "family_size": family_size,
        "priorities": priorities,
    }

    prompt = (
        "You are a Care Health Insurance advisor. Based on the user profile and KB excerpts, "
        "recommend up to 3 suitable products.\n"
        "Reply with JSON only:\n"
        '{"recommendations": [{"product": "...", "why": "...", "caveats": "...", '
        '"document_id": number|null, "page_number": number|null}]}\n\n'
        f"User profile:\n{json.dumps(profile)}\n\n"
        f"Knowledge base excerpts:\n{excerpt_block}\n"
    )

    try:
        response = get_groq_client().chat.completions.create(
            model=GROQ_CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = response.choices[0].message.content or ""
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if fence:
            raw = fence.group(1).strip()
        data = json.loads(raw)
        recommendations = data.get("recommendations") or []
        return {"recommendations": recommendations[:3], "profile": profile}
    except Exception as exc:
        logger.error("Policy recommendation failed: %s", exc)
        fallback = [
            {
                "product": p["title"],
                "why": "Matched your profile from indexed brochures.",
                "caveats": "Review full prospectus for eligibility and limits.",
                "document_id": p["document_id"],
                "page_number": p.get("page_number"),
            }
            for p in product_context[:3]
        ]
        return {
            "recommendations": fallback,
            "profile": profile,
            "error": str(exc),
        }
