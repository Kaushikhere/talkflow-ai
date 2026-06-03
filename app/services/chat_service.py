from fastapi import HTTPException

from app import state
from app.database import (
    create_conversation,
    get_conversation_messages,
    get_file_by_id,
    get_files_for_conversation,
    save_chat_message,
    update_file_conversation,
)
from app.services.groq_client import get_groq_client

MAX_CONTEXT_LENGTH = 30000


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


def generate_reply(
    message: str,
    conversation_id: int | None = None,
    file_ids: list[int] | None = None,
):
    if state.maintenance_mode:
        raise HTTPException(
            status_code=503,
            detail="Server is under maintenance. Please try again shortly.",
        )

    cleaned = message.strip()

    if not cleaned:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty.",
        )

    # Create new conversation if needed
    if conversation_id is None:
        title = cleaned[:50]
        conversation_id = create_conversation(title)

    if file_ids:
        for file_id in file_ids:
            update_file_conversation(file_id, conversation_id)

    # Load previous messages
    history = get_conversation_messages(conversation_id)

    file_context = build_file_context(file_ids, conversation_id)

    system_content = "You are a helpful assistant."
    if file_context:
        system_content = (
            "You are a helpful assistant. "
            "The user has uploaded documents and images that you can reference to answer their questions. "
            "When answering, cite specific information from the uploaded content.\n\n"
            f"{file_context}"
        )

    messages = [
        {
            "role": "system",
            "content": system_content,
        }
    ]

    for item in history:
        messages.append(
            {
                "role": item["role"],
                "content": item["content"],
            }
        )

    messages.append(
        {
            "role": "user",
            "content": cleaned,
        }
    )

    response = get_groq_client().chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages,
    )

    reply = response.choices[0].message.content or ""

    save_chat_message(
        conversation_id,
        "user",
        cleaned,
    )

    save_chat_message(
        conversation_id,
        "assistant",
        reply,
    )

    return {
        "reply": reply,
        "conversation_id": conversation_id,
    }
