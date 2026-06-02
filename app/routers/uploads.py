import uuid
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from pymupdf import open as open_pdf

from app.config import UPLOADS_DIR
from app.database import get_db_connection, utc_now

router = APIRouter()


def extract_pdf_metadata(file_path: Path) -> dict:
    if file_path.suffix.lower() != ".pdf":
        return {}

    try:
        with open_pdf(file_path) as document:
            return {"pdf_pages": document.page_count}
    except Exception:
        # Upload should still succeed even if PDF parsing fails.
        return {"pdf_pages": None}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    safe_name = Path(file.filename or "upload.bin").name
    extension = Path(safe_name).suffix
    stored_name = f"{uuid.uuid4().hex}{extension}"
    destination = UPLOADS_DIR / stored_name

    content = await file.read()
    destination.write_bytes(content)
    uploaded_at = utc_now()
    metadata = extract_pdf_metadata(destination)

    conn = get_db_connection()
    cursor = conn.execute(
        """
        INSERT INTO uploaded_files (original_name, stored_name, size, uploaded_at)
        VALUES (?, ?, ?, ?)
        """,
        (safe_name, stored_name, len(content), uploaded_at),
    )
    conn.commit()
    file_id = cursor.lastrowid
    conn.close()

    return {
        "id": file_id,
        "name": safe_name,
        "size": len(content),
        "uploaded_at": uploaded_at,
        "metadata": metadata,
    }


@router.get("/uploads")
def list_uploads():
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT id, original_name, size, uploaded_at
        FROM uploaded_files
        ORDER BY id DESC
        """
    ).fetchall()
    conn.close()

    files = [
        {
            "id": row["id"],
            "name": row["original_name"],
            "size": row["size"],
            "uploaded_at": row["uploaded_at"],
        }
        for row in rows
    ]
    return {"files": files}
