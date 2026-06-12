"""Follow-up chat grounded exclusively in an uploaded audit policy."""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from app.config import AUDIT_CHAT_MODEL, AUDIT_CONTEXT_CHARS, GROQ_MAX_TOKENS
from app.database import get_uploaded_policy
from app.services.groq_client import get_groq_client

logger = logging.getLogger(__name__)


def _build_audit_system_message(policy: dict) -> str:
    metrics_json = policy.get("raw_extracted_json") or "{}"
    extracted = (policy.get("extracted_text") or "")[:AUDIT_CONTEXT_CHARS]
    filename = policy.get("filename") or "Uploaded policy"

    return (
        "You are a health insurance policy advisor. Answer ONLY using the uploaded "
        "policy data below. Do not use external knowledge or Care brochure information.\n"
        "If the answer is not in the policy data, say you cannot find it in this document.\n"
        "Plain text only. No markdown.\n\n"
        f"Policy file: {filename}\n\n"
        f"Extracted metrics (JSON):\n{metrics_json}\n\n"
        f"Policy text excerpts:\n{extracted}\n"
    )


def stream_audit_chat_events(policy_id: int, message: str) -> Iterator[str]:
    """SSE stream for audit follow-up chat."""
    import json as json_mod

    policy = get_uploaded_policy(policy_id)
    if not policy:
        yield f"data: {json_mod.dumps({'type': 'error', 'detail': 'Policy not found'})}\n\n"
        return

    cleaned = message.strip()
    if not cleaned:
        yield f"data: {json_mod.dumps({'type': 'error', 'detail': 'Message cannot be empty'})}\n\n"
        return

    messages = [
        {"role": "system", "content": _build_audit_system_message(policy)},
        {"role": "user", "content": cleaned},
    ]

    client = get_groq_client()
    try:
        stream = client.chat.completions.create(
            model=AUDIT_CHAT_MODEL,
            messages=messages,
            max_tokens=GROQ_MAX_TOKENS,
            stream=True,
        )
        parts: list[str] = []
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if not delta:
                continue
            parts.append(delta)
            yield f"data: {json_mod.dumps({'type': 'token', 'content': delta})}\n\n"

        reply = "".join(parts)
        yield f"data: {json_mod.dumps({'type': 'done', 'reply': reply, 'policy_id': policy_id})}\n\n"
    except Exception as exc:
        logger.error("Audit chat stream failed: %s", exc)
        yield f"data: {json_mod.dumps({'type': 'error', 'detail': str(exc)})}\n\n"


def generate_audit_chat_reply(policy_id: int, message: str) -> dict:
    """Non-streaming audit follow-up chat."""
    policy = get_uploaded_policy(policy_id)
    if not policy:
        raise ValueError("Policy not found")

    cleaned = message.strip()
    if not cleaned:
        raise ValueError("Message cannot be empty")

    messages = [
        {"role": "system", "content": _build_audit_system_message(policy)},
        {"role": "user", "content": cleaned},
    ]

    response = get_groq_client().chat.completions.create(
        model=AUDIT_CHAT_MODEL,
        messages=messages,
        max_tokens=GROQ_MAX_TOKENS,
    )
    reply = response.choices[0].message.content or ""
    return {"reply": reply, "policy_id": policy_id}
