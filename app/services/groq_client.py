import os
from typing import Any

from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv()

_client = None


def get_groq_client():
    global _client

    if _client is not None:
        return _client

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Missing GROQ_API_KEY in environment variables.",
        )

    try:
        from groq import Groq
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Missing groq package. Install project dependencies first.",
        ) from exc

    _client = Groq(api_key=api_key)
    return _client


def groq_assistant_text(message: Any) -> str:
    """Return assistant visible text; gpt-oss models may use reasoning when content is empty."""
    content = (getattr(message, "content", None) or "").strip()
    if content:
        return content
    return (getattr(message, "reasoning", None) or "").strip()
