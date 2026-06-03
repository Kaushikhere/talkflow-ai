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

    # Conversations
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # Messages
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id)
                REFERENCES conversations(id)
        )
        """
    )

    # Uploaded Files
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS uploaded_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            size INTEGER NOT NULL,
            uploaded_at TEXT NOT NULL,
            extracted_text TEXT,
            conversation_id INTEGER,
            extraction_method TEXT,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id)
        )
        """
    )

    # Migration: Add new columns if they don't exist (for existing databases)
    try:
        cursor.execute("ALTER TABLE uploaded_files ADD COLUMN extracted_text TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE uploaded_files ADD COLUMN conversation_id INTEGER")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute(
            "ALTER TABLE uploaded_files ADD COLUMN extraction_method TEXT"
        )
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


# -------------------------
# Conversation Functions
# -------------------------

def create_conversation(title: str) -> int:
    conn = get_db_connection()

    cursor = conn.execute(
        """
        INSERT INTO conversations
        (title, created_at)
        VALUES (?, ?)
        """,
        (title, utc_now()),
    )

    conversation_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return conversation_id


def get_all_conversations():
    conn = get_db_connection()

    rows = conn.execute(
        """
        SELECT *
        FROM conversations
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    return rows


def clear_all_conversations() -> dict:
    conn = get_db_connection()

    message_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM messages
        """
    ).fetchone()[0]

    conversation_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM conversations
        """
    ).fetchone()[0]

    conn.execute("DELETE FROM messages")
    conn.execute("DELETE FROM conversations")

    conn.commit()
    conn.close()

    return {
        "deleted_conversations": conversation_count,
        "deleted_messages": message_count,
    }


def get_conversation(conversation_id: int):
    conn = get_db_connection()

    row = conn.execute(
        """
        SELECT *
        FROM conversations
        WHERE id = ?
        """,
        (conversation_id,),
    ).fetchone()

    conn.close()

    return row


# -------------------------
# Message Functions
# -------------------------

def save_chat_message(
    conversation_id: int,
    role: str,
    content: str,
) -> None:

    conn = get_db_connection()

    conn.execute(
        """
        INSERT INTO messages
        (
            conversation_id,
            role,
            content,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            conversation_id,
            role,
            content,
            utc_now(),
        ),
    )

    conn.commit()
    conn.close()


def get_conversation_messages(
    conversation_id: int,
):
    conn = get_db_connection()

    rows = conn.execute(
        """
        SELECT role, content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conversation_id,),
    ).fetchall()

    conn.close()

    return rows


# -------------------------
# Upload Functions
# -------------------------

def save_uploaded_file(
    original_name: str,
    stored_name: str,
    size: int,
    extracted_text: str = "",
    conversation_id: int | None = None,
    extraction_method: str | None = None,
) -> dict:

    conn = get_db_connection()
    uploaded_at = utc_now()

    cursor = conn.execute(
        """
        INSERT INTO uploaded_files
        (
            original_name,
            stored_name,
            size,
            uploaded_at,
            extracted_text,
            conversation_id,
            extraction_method
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            original_name,
            stored_name,
            size,
            uploaded_at,
            extracted_text,
            conversation_id,
            extraction_method,
        ),
    )

    conn.commit()
    file_id = cursor.lastrowid
    conn.close()

    return {
        "id": file_id,
        "uploaded_at": uploaded_at,
    }


def get_files_for_conversation(conversation_id: int) -> list:
    """Get all uploaded files associated with a conversation."""
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT id, original_name, extracted_text
        FROM uploaded_files
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conversation_id,),
    ).fetchall()
    conn.close()

    return [
        {
            "id": row["id"],
            "name": row["original_name"],
            "text": row["extracted_text"] or "",
        }
        for row in rows
    ]


def get_file_by_id(file_id: int) -> dict | None:
    """Get a single uploaded file by ID."""
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT id, original_name, extracted_text, conversation_id
        FROM uploaded_files
        WHERE id = ?
        """,
        (file_id,),
    ).fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row["id"],
        "name": row["original_name"],
        "text": row["extracted_text"] or "",
        "conversation_id": row["conversation_id"],
    }


def update_file_conversation(file_id: int, conversation_id: int) -> bool:
    """Link an uploaded file to a conversation."""
    conn = get_db_connection()
    cursor = conn.execute(
        """
        UPDATE uploaded_files
        SET conversation_id = ?
        WHERE id = ?
        """,
        (conversation_id, file_id),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated
