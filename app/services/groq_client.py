import os

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
