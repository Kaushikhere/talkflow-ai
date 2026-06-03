from fastapi import APIRouter

from app.database import (
    clear_all_conversations,
    get_all_conversations,
    get_conversation_messages,
)
from app.models import ChatRequest
from app.services.chat_service import generate_reply

router = APIRouter()


@router.post("/chat")
def chat(request: ChatRequest):

    result = generate_reply(
        request.message,
        request.conversation_id,
        request.file_ids,
    )

    return result


@router.get("/conversations")
def conversations():

    rows = get_all_conversations()

    return {
        "conversations": [
            dict(row)
            for row in rows
        ]
    }


@router.delete("/conversations")
def clear_conversations():
    return clear_all_conversations()


@router.get("/conversations/{conversation_id}")
def conversation_messages(
    conversation_id: int,
):

    rows = get_conversation_messages(
        conversation_id
    )

    return {
        "messages": [
            dict(row)
            for row in rows
        ]
    }
