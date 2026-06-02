from fastapi import APIRouter

from app.database import get_db_connection
from app.models import ChatRequest
from app.services.chat_service import generate_reply

router = APIRouter()


@router.post("/chat")
def chat(request: ChatRequest):
    return {"reply": generate_reply(request.message)}


@router.get("/history")
def get_history():
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, role, content, created_at FROM chat_history ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return {"messages": [dict(row) for row in rows]}


@router.delete("/history")
def clear_history():
    conn = get_db_connection()
    conn.execute("DELETE FROM chat_history")
    conn.commit()
    conn.close()
    return {"ok": True}
