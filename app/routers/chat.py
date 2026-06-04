from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.database import (
    clear_all_conversations,
    delete_conversation,
    get_all_conversations,
    get_conversation_messages,
)
from app.models import ChatRequest
from app.services.chat_service import generate_reply, stream_reply_events

router = APIRouter()


@router.post("/chat")
def chat(request: ChatRequest):
    use_kb = request.use_knowledge_base

    if request.stream:
        return StreamingResponse(
            stream_reply_events(
                request.message,
                request.conversation_id,
                request.file_ids,
                use_knowledge_base=use_kb,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return generate_reply(
        request.message,
        request.conversation_id,
        request.file_ids,
        use_knowledge_base=use_kb,
    )


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


@router.delete("/conversations/{conversation_id}")
def delete_one_conversation(conversation_id: int):
    if not delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"deleted": True, "conversation_id": conversation_id}


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
