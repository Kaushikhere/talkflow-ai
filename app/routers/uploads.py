import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import MAX_UPLOAD_BYTES, UPLOADS_DIR
from app.database import get_db_connection, save_uploaded_file
from app.services.document_extraction import (
    extract_plain_text,
    is_allowed_extension,
    is_image_extension,
    process_image_upload,
    process_pdf_upload,
)

logger = logging.getLogger(__name__)

router = APIRouter()
UPLOAD_CHUNK_SIZE = 1024 * 1024


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    conversation_id: int | None = Form(default=None),
):
    safe_name = Path(file.filename or "upload.bin").name
    extension = Path(safe_name).suffix.lower()

    logger.info("Upload started: %s (extension: %s)", safe_name, extension)

    if not is_allowed_extension(extension):
        logger.warning("Rejected upload: unsupported extension %s", extension)
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Allowed: PDF, TXT, and images "
                "(JPG, PNG, WebP, GIF, BMP)."
            ),
        )

    stored_name = f"{uuid.uuid4().hex}{extension}"
    destination = UPLOADS_DIR / stored_name

    size = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    destination.unlink(missing_ok=True)
                    logger.warning("Rejected upload: file too large (%d bytes)", size)
                    raise HTTPException(
                        status_code=400,
                        detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    )
                output.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Upload write failed for %s: %s", safe_name, exc)
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    logger.info("File saved: %s (%d bytes) -> %s", safe_name, size, stored_name)

    metadata: dict = {}
    extracted_text = ""
    extraction_method: str | None = None

    try:
        if extension == ".pdf":
            extracted_text, metadata, extraction_method = process_pdf_upload(destination)
        elif is_image_extension(extension):
            extracted_text, metadata, extraction_method = process_image_upload(destination)
        elif extension == ".txt":
            extracted_text, metadata, extraction_method = extract_plain_text(destination)
    except Exception as exc:
        logger.error("Content extraction failed for %s: %s", safe_name, exc)
        metadata["error"] = "extraction_failed"
        metadata["error_detail"] = str(exc)

    saved_file = save_uploaded_file(
        safe_name,
        stored_name,
        size,
        extracted_text=extracted_text,
        conversation_id=conversation_id,
        extraction_method=extraction_method,
    )

    text_preview = ""
    if extracted_text:
        text_preview = extracted_text[:500] + ("..." if len(extracted_text) > 500 else "")

    logger.info(
        "Upload complete: id=%d, has_text=%s, method=%s",
        saved_file["id"],
        bool(extracted_text.strip()),
        extraction_method,
    )

    return {
        "id": saved_file["id"],
        "name": safe_name,
        "size": size,
        "uploaded_at": saved_file["uploaded_at"],
        "metadata": metadata,
        "has_text": bool(extracted_text.strip()),
        "text_preview": text_preview,
        "extraction_method": extraction_method,
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
        raise HTTPException(status_code=404, detail="File not found")

    return {"success": True, "file_id": file_id, "conversation_id": conversation_id}
