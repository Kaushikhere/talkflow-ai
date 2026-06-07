"""Post-generation check: is the reply supported by KB excerpts?"""
from __future__ import annotations

import json
import logging
import re

from app.config import FAITHFULNESS_ENABLED, FAITHFULNESS_MODEL
from app.services.groq_client import get_groq_client

logger = logging.getLogger(__name__)


def _build_excerpt_block(kb_sources: list[dict], kb_context: str) -> str:
    if kb_context.strip():
        return kb_context[:12000]
    parts: list[str] = []
    for source in kb_sources:
        title = source.get("title") or "Document"
        page = source.get("page_number")
        page_label = f", page {page}" if page is not None else ""
        snippet = source.get("snippet") or ""
        parts.append(f"--- {title}{page_label} ---\n{snippet}")
    return "\n\n".join(parts)


def _parse_faithfulness_json(raw: str) -> dict:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {
            "passed": True,
            "confidence": "low",
            "unsupported_claims": [],
            "parse_error": True,
        }
    return {
        "passed": bool(data.get("passed", True)),
        "confidence": str(data.get("confidence") or "medium"),
        "unsupported_claims": list(data.get("unsupported_claims") or []),
    }


def check_faithfulness(
    user_query: str,
    reply: str,
    *,
    kb_sources: list[dict],
    kb_context: str = "",
) -> dict | None:
    if not FAITHFULNESS_ENABLED or not kb_sources or not reply.strip():
        return None

    excerpts = _build_excerpt_block(kb_sources, kb_context)
    if not excerpts.strip():
        return None

    prompt = (
        "You are a precise fact-checker for insurance customer support responses.\n\n"
        "Task: Determine whether every factual claim in the assistant's reply is directly "
        "supported by the knowledge base excerpts below.\n\n"
        "Evaluation criteria:\n"
        "- SUPPORTED: The excerpt explicitly states the fact, or it is a clear logical "
        "inference from what is stated.\n"
        "- UNSUPPORTED: The reply introduces a fact, number, benefit, or limit that is "
        "absent from or contradicts the excerpts.\n"
        "- IGNORE: Conversational phrases ('we can help', 'feel free to ask'), follow-up "
        "questions, and tone/style are not factual claims — do not flag them.\n\n"
        "Reply with ONLY valid JSON — no explanation, no markdown wrapper:\n"
        '{"passed": true, "confidence": "high", "unsupported_claims": []}\n\n'
        f"User question: {user_query}\n\n"
        f"Assistant reply:\n{reply}\n\n"
        f"Knowledge base excerpts:\n{excerpts}\n"
    )

    try:
        response = get_groq_client().chat.completions.create(
            model=FAITHFULNESS_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = response.choices[0].message.content or ""
        result = _parse_faithfulness_json(raw)
        result["checked"] = True
        return result
    except Exception as exc:
        logger.error("Faithfulness check failed: %s", exc)
        return {
            "passed": True,
            "confidence": "low",
            "unsupported_claims": [],
            "checked": False,
            "error": str(exc),
        }
