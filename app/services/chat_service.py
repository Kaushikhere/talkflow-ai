import os

from fastapi import HTTPException
from groq import Groq

from app import state
from app.database import save_chat_message

api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    raise RuntimeError("Missing GROQ_API_KEY in environment variables.")

client = Groq(api_key=api_key)


def generate_reply(message: str) -> str:
    if state.maintenance_mode:
        raise HTTPException(
            status_code=503,
            detail="Server is under maintenance. Please try again shortly.",
        )

    cleaned = message.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": cleaned},
        ],
    )
    reply = response.choices[0].message.content or ""

    save_chat_message("user", cleaned)
    save_chat_message("assistant", reply)
    return reply
