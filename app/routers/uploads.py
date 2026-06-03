import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from pymupdf import open as open_pdf

from app.config import UPLOADS_DIR
from app.database import get_db_connection, save_uploaded_file

router = APIRouter()
UPLOAD_CHUNK_SIZE = 1024 * 1024
MAX_TEXT_LENGTH = 50000


def extract_pdf_text(file_path: Path) -> str:
    """Extract text content from PDF using PyMuPDF."""
    if file_path.suffix.lower() != ".pdf":
        return ""

    try:
        text_content = []
        with open_pdf(file_path) as document:
            for page in document:
                page_text = page.get_text()
                if page_text:
                    text_content.append(page_text.strip())
        return "\n\n".join(text_content)
    except Exception:
        return ""


def extract_pdf_with_ocr(file_path: Path) -> str:
    """OCR fallback for scanned PDFs using pytesseract."""
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError:
        return ""

    try:
        images = convert_from_path(str(file_path))
        text_content = []
        for img in images:
            page_text = pytesseract.image_to_string(img)
            if page_text:
                text_content.append(page_text.strip())
        return "\n\n".join(text_content)
    except Exception:
        return ""


def extract_pdf_metadata(file_path: Path) -> dict:
    if file_path.suffix.lower() != ".pdf":
        return {}

    try:
        with open_pdf(file_path) as document:
            return {"pdf_pages": document.page_count}
    except Exception:
        return {"pdf_pages": None}


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    conversation_id: int | None = Form(default=None),
):
    safe_name = Path(file.filename or "upload.bin").name
    extension = Path(safe_name).suffix
    stored_name = f"{uuid.uuid4().hex}{extension}"
    destination = UPLOADS_DIR / stored_name

    size = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                size += len(chunk)
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    metadata = extract_pdf_metadata(destination)

    extracted_text = ""
    if extension.lower() == ".pdf":
        extracted_text = extract_pdf_text(destination)
        if not extracted_text.strip():
            extracted_text = extract_pdf_with_ocr(destination)
        if len(extracted_text) > MAX_TEXT_LENGTH:
            extracted_text = extracted_text[:MAX_TEXT_LENGTH] + "\n\n[Text truncated...]"

    saved_file = save_uploaded_file(
        safe_name,
        stored_name,
        size,
        extracted_text=extracted_text,
        conversation_id=conversation_id,
    )

    text_preview = ""
    if extracted_text:
        text_preview = extracted_text[:500] + ("..." if len(extracted_text) > 500 else "")

    return {
        "id": saved_file["id"],
        "name": safe_name,
        "size": size,
        "uploaded_at": saved_file["uploaded_at"],
        "metadata": metadata,
        "has_text": bool(extracted_text),
        "text_preview": text_preview,
    }


@router.get("/uploads")
def list_uploads(conversation_id: int | None = None):
    conn = get_db_connection()

    if conversation_id is not None:
        rows = conn.execute(
            """
            SELECT id, original_name, size, uploaded_at, conversation_id,
                   CASE WHEN extracted_text IS NOT NULL AND extracted_text != '' THEN 1 ELSE 0 END as has_text
            FROM uploaded_files
            WHERE conversation_id = ?
            ORDER BY id DESC
            """,
            (conversation_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, original_name, size, uploaded_at, conversation_id,
                   CASE WHEN extracted_text IS NOT NULL AND extracted_text != '' THEN 1 ELSE 0 END as has_text
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
            "conversation_id": row["conversation_id"],
            "has_text": bool(row["has_text"]),
        }
        for row in rows
    ]
    return {"files": files}


@router.get("/uploads/{file_id}")
def get_upload(file_id: int):
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT id, original_name, size, uploaded_at, conversation_id, extracted_text
        FROM uploaded_files
        WHERE id = ?
        """,
        (file_id,),
    ).fetchone()
    conn.close()

    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="File not found")

    return {
        "id": row["id"],
        "name": row["original_name"],
        "size": row["size"],
        "uploaded_at": row["uploaded_at"],
        "conversation_id": row["conversation_id"],
        "extracted_text": row["extracted_text"] or "",
    }


@router.put("/uploads/{file_id}/conversation")
def link_upload_to_conversation(file_id: int, conversation_id: int):
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

    if not updated:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="File not found")

    return {"success": True, "file_id": file_id, "conversation_id": conversation_id}
