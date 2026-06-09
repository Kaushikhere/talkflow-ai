"""Generate concise summaries of long chat conversations."""
from __future__ import annotations

import logging

from fastapi import HTTPException

from app.config import GROQ_CHAT_MODEL, GROQ_MAX_TOKENS
from app.database import get_conversation, get_conversation_messages
from app.services.groq_client import get_groq_client

logger = logging.getLogger(__name__)

MAX_SUMMARY_INPUT_CHARS = 24_000
MIN_MESSAGES_TO_SUMMARIZE = 2

_SUMMARY_SYSTEM = """You summarize Care Health Insurance customer support chats between a user and advisor Aria.

Write a clear, structured summary for someone who was not in the conversation.

Include when present:
- Main topic and what the user wanted
- Products or plans discussed (use exact Care product names from the chat)
- Key facts, numbers, waiting periods, or coverage points mentioned
- Decisions, recommendations, or open questions left unresolved
- Any uploaded documents the user referenced

Rules:
- Plain text only. No markdown asterisks or hashtags.
- Use short section titles on their own line, then hyphen bullets.
- Be factual — do not invent details not in the transcript.
- Keep the summary under 350 words unless the chat was exceptionally long."""


def _format_transcript(messages: list) -> str:
    parts: list[str] = []
    for item in messages:
        role = item["role"] if isinstance(item, dict) else item["role"]
        content = item["content"] if isinstance(item, dict) else item["content"]
        label = "Customer" if role == "user" else "Aria"
        parts.append(f"{label}: {content.strip()}")
    return "\n\n".join(parts)


def summarize_conversation(conversation_id: int) -> dict:
    conv = get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = get_conversation_messages(conversation_id)
    if len(messages) < MIN_MESSAGES_TO_SUMMARIZE:
        raise HTTPException(
            status_code=400,
            detail="Need at least two messages before summarizing this chat.",
        )

    transcript = _format_transcript(messages)
    truncated = False
    if len(transcript) > MAX_SUMMARY_INPUT_CHARS:
        truncated = True
        transcript = (
            "[Note: earliest part of this conversation was omitted due to length.]\n\n"
            + transcript[-MAX_SUMMARY_INPUT_CHARS:]
        )

    try:
        response = get_groq_client().chat.completions.create(
            model=GROQ_CHAT_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Summarize this conversation ({len(messages)} messages):\n\n"
                        f"{transcript}"
                    ),
                },
            ],
            max_tokens=min(GROQ_MAX_TOKENS, 1024),
        )
    except Exception as exc:
        logger.exception("Conversation summary failed for id=%s", conversation_id)
        raise HTTPException(
            status_code=502,
            detail=f"Summary generation failed: {exc}",
        ) from exc

    summary = (response.choices[0].message.content or "").strip()
    if not summary:
        raise HTTPException(status_code=502, detail="Summary generation returned empty text")

    title = conv["title"] if conv else ""

    return {
        "conversation_id": conversation_id,
        "title": title,
        "message_count": len(messages),
        "truncated": truncated,
        "summary": summary,
    }
