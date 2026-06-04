import sqlite3
from datetime import datetime, timezone

from app.config import CHROMA_PATH, DB_PATH, KB_DATA_DIR, KB_EXTERNAL_DIR, UPLOADS_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_storage() -> None:
    UPLOADS_DIR.mkdir(exist_ok=True)
    KB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    KB_EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)


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

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url TEXT NOT NULL UNIQUE,
            title TEXT,
            content_hash TEXT,
            raw_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            chunk_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_ingest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            seed_urls TEXT,
            documents_added INTEGER DEFAULT 0,
            chunks_added INTEGER DEFAULT 0,
            error_message TEXT
        )
        """
    )

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


def delete_conversation(conversation_id: int) -> bool:
    conn = get_db_connection()

    row = conn.execute(
        "SELECT id FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()

    if not row:
        conn.close()
        return False

    conn.execute(
        "DELETE FROM messages WHERE conversation_id = ?",
        (conversation_id,),
    )
    conn.execute(
        "UPDATE uploaded_files SET conversation_id = NULL WHERE conversation_id = ?",
        (conversation_id,),
    )
    conn.execute(
        "DELETE FROM conversations WHERE id = ?",
        (conversation_id,),
    )

    conn.commit()
    conn.close()
    return True


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


# -------------------------
# Knowledge base documents
# -------------------------


def list_kb_documents(*, source: str | None = None) -> list[dict]:
    """List KB documents; source=external limits to data/kb/external/."""
    conn = get_db_connection()
    if source == "external":
        rows = conn.execute(
            """
            SELECT id, source_url, title, raw_path, status, chunk_count,
                   created_at, updated_at
            FROM kb_documents
            WHERE raw_path LIKE '%external%'
            ORDER BY title, id
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, source_url, title, raw_path, status, chunk_count,
                   created_at, updated_at
            FROM kb_documents
            ORDER BY title, id
            """
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_kb_stats() -> dict:
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'indexed' THEN 1 ELSE 0 END) AS indexed,
               SUM(CASE WHEN status = 'indexed' THEN chunk_count ELSE 0 END) AS chunks
        FROM kb_documents
        """
    ).fetchone()
    conn.close()
    return {
        "documents_total": row["total"] if row else 0,
        "documents_indexed": row["indexed"] if row else 0,
        "chunks_total": row["chunks"] if row and row["chunks"] else 0,
    }


def get_last_kb_ingest_run() -> dict | None:
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT id, started_at, finished_at, status, seed_urls,
               documents_added, chunks_added, error_message
        FROM kb_ingest_runs
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_running_kb_ingest_run() -> dict | None:
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT id, started_at, status
        FROM kb_ingest_runs
        WHERE status = 'running'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_indexed_kb_documents() -> list[dict]:
    """All KB documents with status indexed (id + title for retrieval)."""
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT id, title, source_url, raw_path
        FROM kb_documents
        WHERE status = 'indexed'
        ORDER BY id
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_kb_document_by_source_url(source_url: str) -> dict | None:
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT id, source_url, title, content_hash, raw_path, status, chunk_count,
               created_at, updated_at
        FROM kb_documents
        WHERE source_url = ?
        """,
        (source_url,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def insert_kb_document(
    *,
    source_url: str,
    title: str,
    content_hash: str,
    raw_path: str,
    status: str = "pending",
) -> int:
    now = utc_now()
    conn = get_db_connection()
    cursor = conn.execute(
        """
        INSERT INTO kb_documents
        (source_url, title, content_hash, raw_path, status, chunk_count, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (source_url, title, content_hash, raw_path, status, now, now),
    )
    conn.commit()
    document_id = cursor.lastrowid
    conn.close()
    return document_id


def update_kb_document(
    document_id: int,
    *,
    status: str | None = None,
    chunk_count: int | None = None,
    content_hash: str | None = None,
    title: str | None = None,
) -> None:
    fields: list[str] = ["updated_at = ?"]
    values: list = [utc_now()]

    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if chunk_count is not None:
        fields.append("chunk_count = ?")
        values.append(chunk_count)
    if content_hash is not None:
        fields.append("content_hash = ?")
        values.append(content_hash)
    if title is not None:
        fields.append("title = ?")
        values.append(title)

    values.append(document_id)
    conn = get_db_connection()
    conn.execute(
        f"UPDATE kb_documents SET {', '.join(fields)} WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()


# -------------------------
# Knowledge base ingest runs
# -------------------------


def create_kb_ingest_run(seed_urls: str) -> int:
    conn = get_db_connection()
    cursor = conn.execute(
        """
        INSERT INTO kb_ingest_runs
        (started_at, status, seed_urls, documents_added, chunks_added)
        VALUES (?, 'running', ?, 0, 0)
        """,
        (utc_now(), seed_urls),
    )
    conn.commit()
    run_id = cursor.lastrowid
    conn.close()
    return run_id


def finish_kb_ingest_run(
    run_id: int,
    *,
    status: str,
    documents_added: int = 0,
    chunks_added: int = 0,
    error_message: str | None = None,
) -> None:
    conn = get_db_connection()
    conn.execute(
        """
        UPDATE kb_ingest_runs
        SET finished_at = ?, status = ?, documents_added = ?, chunks_added = ?,
            error_message = ?
        WHERE id = ?
        """,
        (utc_now(), status, documents_added, chunks_added, error_message, run_id),
    )
    conn.commit()
    conn.close()
