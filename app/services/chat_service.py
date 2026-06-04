import json
import logging
from collections.abc import Iterator

from fastapi import HTTPException

from app import state

logger = logging.getLogger(__name__)
from app.database import (
    create_conversation,
    get_conversation_messages,
    get_file_by_id,
    get_files_for_conversation,
    save_chat_message,
    update_file_conversation,
)
from app.config import KB_ENABLED, MAX_CHAT_HISTORY
from app.services.groq_client import get_groq_client
from app.services.kb_retrieval import build_kb_context

MAX_CONTEXT_LENGTH = 30000
GROQ_MODEL = "llama-3.1-8b-instant"


def build_file_context(file_ids: list[int] | None, conversation_id: int | None) -> str:
    """Build context string from uploaded files."""
    files_data = []

    if file_ids:
        for file_id in file_ids:
            file_data = get_file_by_id(file_id)
            if file_data and file_data["text"]:
                files_data.append(file_data)
                if conversation_id and not file_data["conversation_id"]:
                    update_file_conversation(file_id, conversation_id)

    if conversation_id and not file_ids:
        conv_files = get_files_for_conversation(conversation_id)
        for f in conv_files:
            if f["text"] and f not in files_data:
                files_data.append(f)

    if not files_data:
        return ""

    context_parts = ["The user has uploaded the following documents and images:\n"]
    total_length = len(context_parts[0])

    for file_data in files_data:
        file_header = f"\n--- Document: {file_data['name']} ---\n"
        file_text = file_data["text"]

        remaining = MAX_CONTEXT_LENGTH - total_length - len(file_header) - 100
        if remaining <= 0:
            context_parts.append("\n[Additional documents truncated due to length...]")
            break

        if len(file_text) > remaining:
            file_text = file_text[:remaining] + "\n[Content truncated...]"

        context_parts.append(file_header)
        context_parts.append(file_text)
        total_length += len(file_header) + len(file_text)

    context_parts.append("\n--- End of Documents ---\n")
    return "".join(context_parts)


def _trim_history(history: list) -> list:
    if MAX_CHAT_HISTORY <= 0 or len(history) <= MAX_CHAT_HISTORY:
        return history
    return history[-MAX_CHAT_HISTORY:]


def _prepare_chat(
    message: str,
    conversation_id: int | None,
    file_ids: list[int] | None,
    *,
    use_knowledge_base: bool = True,
) -> tuple[int, list[dict], str, list[dict], bool]:
    if state.maintenance_mode:
        raise HTTPException(
            status_code=503,
            detail="Server is under maintenance. Please try again shortly.",
        )

    cleaned = message.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    if conversation_id is None:
        conversation_id = create_conversation(cleaned[:50])

    if file_ids:
        for file_id in file_ids:
            update_file_conversation(file_id, conversation_id)

    history = _trim_history(get_conversation_messages(conversation_id))
    file_context = build_file_context(file_ids, conversation_id)
    kb_context = ""
    kb_sources: list[dict] = []
    kb_used = KB_ENABLED and use_knowledge_base

    if kb_used:
        try:
            kb_context, kb_sources = build_kb_context(cleaned)
        except Exception as exc:
            logger.error("KB retrieval failed, continuing without KB context: %s", exc)

    if kb_context:
        system_content = (
            "You are a Care Health Insurance product assistant. "
            "Use only the knowledge base excerpts below to answer.\n"
            "Give a clear, structured answer (overview, key benefits/features, "
            "coverage highlights, optional add-ons, eligibility or limits when shown). "
            "Use bullet points or short sections. Cite document titles and page numbers.\n"
            "Do not end with vague disclaimers that excerpts are incomplete or that "
            "more detailed information is required unless the user asked for something "
            "specific that is truly absent from the excerpts.\n"
            "If a detail is not in the excerpts, say it is not stated in the indexed "
            "documents for that product.\n"
            f"{kb_context}"
        )
        if file_context:
            system_content += (
                "\nThe user has also uploaded the following files for this conversation:\n"
                f"{file_context}"
            )
    elif file_context:
        system_content = (
            "You are a helpful assistant. Answer using only the user's uploaded files below.\n"
            f"{file_context}"
        )
    elif not use_knowledge_base and KB_ENABLED:
        system_content = (
            "You are a helpful general assistant. "
            "The user has turned OFF the Care Health Insurance product knowledge base "
            "for this message.\n"
            "Rules for this reply:\n"
            "- Do NOT use Care brochure or policy details from earlier messages in this chat.\n"
            "- Do NOT cite Care product names, waiting periods, sum insured, or policy terms "
            "as if you had official documents.\n"
            "- If asked about a specific Care insurance product, say they should enable "
            "'Use Care product KB' in the sidebar, or upload the document.\n"
            "- You may answer general knowledge questions unrelated to Care policies.\n"
        )
    else:
        system_content = "You are a helpful assistant."

    messages = [{"role": "system", "content": system_content}]
    for item in history:
        messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": cleaned})

    return conversation_id, messages, cleaned, kb_sources, kb_used


def generate_reply(
    message: str,
    conversation_id: int | None = None,
    file_ids: list[int] | None = None,
    *,
    use_knowledge_base: bool = True,
):
    conversation_id, messages, cleaned, kb_sources, kb_used = _prepare_chat(
        message,
        conversation_id,
        file_ids,
        use_knowledge_base=use_knowledge_base,
    )

    response = get_groq_client().chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
    )

    reply = response.choices[0].message.content or ""

    save_chat_message(conversation_id, "user", cleaned)
    save_chat_message(conversation_id, "assistant", reply)

    result = {
        "reply": reply,
        "conversation_id": conversation_id,
    }
    result["kb_enabled"] = KB_ENABLED
    result["kb_used"] = kb_used
    if kb_sources:
        result["kb_sources"] = kb_sources
    return result


def stream_reply_events(
    message: str,
    conversation_id: int | None = None,
    file_ids: list[int] | None = None,
    *,
    use_knowledge_base: bool = True,
) -> Iterator[str]:
    """Server-Sent Events: token chunks, then done payload with metadata."""
    conversation_id, messages, cleaned, kb_sources, kb_used = _prepare_chat(
        message,
        conversation_id,
        file_ids,
        use_knowledge_base=use_knowledge_base,
    )

    client = get_groq_client()
    stream = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        stream=True,
    )

    parts: list[str] = []
    try:
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if not delta:
                continue
            parts.append(delta)
            yield f"data: {json.dumps({'type': 'token', 'content': delta})}\n\n"
    except Exception as exc:
        logger.error("Groq stream failed: %s", exc)
        yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"
        return

    reply = "".join(parts)
    save_chat_message(conversation_id, "user", cleaned)
    save_chat_message(conversation_id, "assistant", reply)

    done_payload = {
        "type": "done",
        "reply": reply,
        "conversation_id": conversation_id,
    }
    done_payload["kb_enabled"] = KB_ENABLED
    done_payload["kb_used"] = kb_used
    if kb_sources:
        done_payload["kb_sources"] = kb_sources

    yield f"data: {json.dumps(done_payload)}\n\n"
