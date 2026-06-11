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
from app.config import (
    FAITHFULNESS_ENABLED,
    GROQ_CHAT_MODEL,
    GROQ_MAX_TOKENS,
    KB_ENABLED,
    MAX_CHAT_HISTORY,
)
from app.services.faithfulness import check_faithfulness
from app.services.groq_client import get_groq_client
from app.services.kb_retrieval import build_kb_context

MAX_CONTEXT_LENGTH = 30000

_CARE_AGENT_BASE = """You are Aria, a senior insurance advisor at Care Health Insurance. You explain coverage clearly and warmly — like a trusted advisor, not a brochure dump.

## Answer depth (most important)
- Default to thorough, detailed answers. Short replies are only for simple greetings or high-level catalog lists.
- Use ALL relevant information from <context>. Never give a partial or one-line answer when the context supports more.
- For questions about a specific plan, benefit, waiting period, coverage, claim process, or eligibility: give a FULL answer (typically 400–800 words when the context is rich).
  Include every applicable detail from the context, organized into sections such as:
  Overview, Key benefits, Coverage and limits, Waiting periods, Optional add-ons, Eligibility, Exclusions — only where the context provides them.
- Cover each section with multiple bullet points when the context has the data. Do not collapse a section into one sentence.
- Do not summarize away important numbers, limits, periods, or feature names that appear in the context.
- Do not say "contact us for more details" or "information may vary" when the context already contains the answer.
- Only stay brief (~150–250 words) for broad catalog questions like "what plans do you have" or "list all products".

## Formatting
- Plain text only. No asterisks, markdown, hashtags, or bold syntax.
- Use short section titles on their own line, then hyphen bullets (-) for lists.
- Group many plans by category; one line per plan in catalog answers only.
- For Explore travel variants in catalog mode: one intro, then region names — no repeated paragraphs.
- Leave a blank line between sections.

## How you communicate
- Lead with a clear opening sentence, then deliver the full substance.
- Never use filler ("Great question!", "I'd be happy to help", "Each plan has unique features").
- Match the customer's energy: reassuring, analytical, or brief as needed.

## Hard rules
1. Use real product names from [PRODUCTS IN CONTEXT] and from <context> excerpts — never "Plan 1", "Plan 2", or placeholders.
2. Never mention documents, brochures, page numbers, or excerpts.
3. Do not invent coverage details absent from the context.
4. End with one short follow-up question."""

_KB_DEPTH_INSTRUCTION = """[KNOWLEDGE BASE ACTIVE]
The <context> block contains retrieved excerpts for this question. Your job is to synthesize them into one complete, detailed, customer-ready answer.
- Read every excerpt before writing.
- If multiple excerpts mention the same product, merge them — do not ignore later excerpts.
- Prefer completeness over brevity unless the user only asked for a high-level list.
- When excerpts contain tables, limits, or waiting periods, reproduce those specifics in your answer."""

_KB_EMPTY_INSTRUCTION = """[KNOWLEDGE BASE ACTIVE — NO MATCHING EXCERPTS]
No relevant product excerpts were retrieved for this question.
- Do not invent Care policy details.
- Tell the user you could not find matching information in the product library for their question.
- Suggest they name a specific plan (e.g. Care Supreme, Explore) or rephrase the question.
- End with one clarifying follow-up question."""


def _build_care_system_message(
    *,
    kb_context: str = "",
    file_context: str = "",
    product_names: list[str] | None = None,
) -> str:
    sections = [_CARE_AGENT_BASE]

    if kb_context.strip():
        sections.append(_KB_DEPTH_INSTRUCTION)

    if product_names:
        names_line = ", ".join(product_names)
        sections.append(f"[PRODUCTS IN CONTEXT: {names_line}]\nUse these exact names in your reply.")

    if kb_context.strip():
        sections.append(f"<context>\n{kb_context.strip()}\n</context>")

    if file_context.strip():
        sections.append(
            "<user_uploads>\n"
            "The user has also shared the following files:\n"
            f"{file_context.strip()}\n"
            "</user_uploads>"
        )
    return "\n\n".join(sections)


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
) -> tuple[int, list[dict], str, list[dict], bool, str]:
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
    kb_product_names: list[str] = []
    kb_used = KB_ENABLED and use_knowledge_base

    if kb_used:
        try:
            kb_context, kb_sources, kb_product_names = build_kb_context(cleaned)
        except Exception as exc:
            logger.error("KB retrieval failed, continuing without KB context: %s", exc)

    if kb_context:
        system_content = _build_care_system_message(
            kb_context=kb_context,
            file_context=file_context,
            product_names=kb_product_names,
        )
    elif kb_used:
        empty_sections = [_CARE_AGENT_BASE, _KB_EMPTY_INSTRUCTION]
        if file_context.strip():
            empty_sections.append(
                "<user_uploads>\n"
                "The user has also shared the following files:\n"
                f"{file_context.strip()}\n"
                "</user_uploads>"
            )
        system_content = "\n\n".join(empty_sections)
    elif file_context:
        system_content = _build_care_system_message(file_context=file_context)
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

    return conversation_id, messages, cleaned, kb_sources, kb_used, kb_context


def generate_reply(
    message: str,
    conversation_id: int | None = None,
    file_ids: list[int] | None = None,
    *,
    use_knowledge_base: bool = True,
):
    conversation_id, messages, cleaned, kb_sources, kb_used, kb_context = _prepare_chat(
        message,
        conversation_id,
        file_ids,
        use_knowledge_base=use_knowledge_base,
    )

    response = get_groq_client().chat.completions.create(
        model=GROQ_CHAT_MODEL,
        messages=messages,
        max_tokens=GROQ_MAX_TOKENS,
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
    if FAITHFULNESS_ENABLED:
        faithfulness = check_faithfulness(
            cleaned, reply, kb_sources=kb_sources, kb_context=kb_context
        )
        if faithfulness:
            result["faithfulness"] = faithfulness
    return result


def stream_reply_events(
    message: str,
    conversation_id: int | None = None,
    file_ids: list[int] | None = None,
    *,
    use_knowledge_base: bool = True,
) -> Iterator[str]:
    """Server-Sent Events: token chunks, then done payload with metadata."""
    conversation_id, messages, cleaned, kb_sources, kb_used, kb_context = _prepare_chat(
        message,
        conversation_id,
        file_ids,
        use_knowledge_base=use_knowledge_base,
    )

    client = get_groq_client()
    stream = client.chat.completions.create(
        model=GROQ_CHAT_MODEL,
        messages=messages,
        max_tokens=GROQ_MAX_TOKENS,
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
    if FAITHFULNESS_ENABLED:
        if kb_sources:
            yield f"data: {json.dumps({'type': 'verifying'})}\n\n"
        faithfulness = check_faithfulness(
            cleaned, reply, kb_sources=kb_sources, kb_context=kb_context
        )
        if faithfulness:
            done_payload["faithfulness"] = faithfulness

    yield f"data: {json.dumps(done_payload)}\n\n"
