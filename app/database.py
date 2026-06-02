import sqlite3
from datetime import datetime, timezone

from app.config import DB_PATH, UPLOADS_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_storage() -> None:
    UPLOADS_DIR.mkdir(exist_ok=True)


def initialize_database() -> None:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS uploaded_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            size INTEGER NOT NULL,
            uploaded_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def save_chat_message(role: str, content: str) -> None:
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO chat_history (role, content, created_at) VALUES (?, ?, ?)",
        (role, content, utc_now()),
    )
    conn.commit()
    conn.close()
