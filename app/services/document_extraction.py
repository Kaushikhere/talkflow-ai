import base64
import logging
import mimetypes
from pathlib import Path

from pymupdf import open as open_pdf

from app.config import (
    GROQ_VISION_MODEL,
    MAX_IMAGE_BYTES,
    MAX_TEXT_LENGTH,
    OCR_MIN_CHARS,
    TESSERACT_CMD,
)
from app.services.groq_client import get_groq_client

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf", ".txt"}

VISION_PROMPT = (
    "Describe this image in detail for question-answering. "
    "Include all visible text, objects, people, charts, labels, and relevant context."
)


def is_image_extension(extension: str) -> bool:
    return extension.lower() in IMAGE_EXTENSIONS


def is_allowed_extension(extension: str) -> bool:
    return extension.lower() in ALLOWED_EXTENSIONS


def truncate_text(text: str) -> str:
    if len(text) <= MAX_TEXT_LENGTH:
        return text
    return text[:MAX_TEXT_LENGTH] + "\n\n[Text truncated...]"


def _configure_tesseract() -> bool:
    """Point pytesseract at the Windows install path when not on PATH."""
    if not TESSERACT_CMD:
        logger.debug("Tesseract not configured: TESSERACT_CMD is not set")
        return False
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
        logger.debug("Tesseract configured at: %s", TESSERACT_CMD)
        return True
    except ImportError:
        logger.warning("pytesseract package not installed, OCR unavailable")
        return False


def extract_pdf_text(file_path: Path) -> str:
    if file_path.suffix.lower() != ".pdf":
        return ""

    try:
        text_content = []
        with open_pdf(file_path) as document:
            for page in document:
                page_text = page.get_text()
                if page_text:
                    text_content.append(page_text.strip())
        result = "\n\n".join(text_content)
        logger.debug("Extracted %d chars from PDF via PyMuPDF", len(result))
        return result
    except Exception as exc:
        logger.error("PDF text extraction failed for %s: %s", file_path.name, exc)
        return ""


def extract_pdf_with_ocr(file_path: Path) -> str:
    if not _configure_tesseract():
        logger.info("Skipping PDF OCR: Tesseract not available")
        return ""

    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as exc:
        logger.warning("PDF OCR dependencies missing (pdf2image/pytesseract): %s", exc)
        return ""

    try:
        logger.debug("Converting PDF to images for OCR: %s", file_path.name)
        images = convert_from_path(str(file_path))
        text_content = []
        for img in images:
            page_text = pytesseract.image_to_string(img)
            if page_text:
                text_content.append(page_text.strip())
        result = "\n\n".join(text_content)
        logger.debug("OCR extracted %d chars from PDF", len(result))
        return result
    except Exception as exc:
        logger.error("PDF OCR failed for %s: %s", file_path.name, exc)
        return ""


def extract_pdf_metadata(file_path: Path) -> dict:
    if file_path.suffix.lower() != ".pdf":
        return {}

    try:
        with open_pdf(file_path) as document:
            return {"file_type": "pdf", "pdf_pages": document.page_count}
    except Exception as exc:
        logger.error("Failed to extract PDF metadata for %s: %s", file_path.name, exc)
        return {"file_type": "pdf", "pdf_pages": None}


def process_pdf_upload(file_path: Path) -> tuple[str, dict, str | None]:
    metadata = extract_pdf_metadata(file_path)
    extracted_text = extract_pdf_text(file_path)
    extraction_method = "pymupdf" if extracted_text.strip() else None

    if not extracted_text.strip():
        extracted_text = extract_pdf_with_ocr(file_path)
        extraction_method = "ocr" if extracted_text.strip() else None

    extracted_text = truncate_text(extracted_text)
    return extracted_text, metadata, extraction_method


def extract_image_text(file_path: Path) -> str:
    if not _configure_tesseract():
        logger.info("Skipping image OCR: Tesseract not available")
        return ""

    try:
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        logger.warning("Image OCR dependencies missing (PIL/pytesseract): %s", exc)
        return ""

    try:
        logger.debug("Running OCR on image: %s", file_path.name)
        with Image.open(file_path) as img:
            result = pytesseract.image_to_string(img).strip()
            logger.debug("Image OCR extracted %d chars", len(result))
            return result
    except Exception as exc:
        logger.error("Image OCR failed for %s: %s", file_path.name, exc)
        return ""


def _mime_for_path(file_path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(file_path))
    if mime and mime.startswith("image/"):
        return mime
    ext = file_path.suffix.lower()
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    return mapping.get(ext, "image/jpeg")


def describe_image_with_vision(file_path: Path) -> str:
    file_size = file_path.stat().st_size
    if file_size > MAX_IMAGE_BYTES:
        logger.info(
            "Skipping vision analysis: image %s is %d bytes (max %d)",
            file_path.name,
            file_size,
            MAX_IMAGE_BYTES,
        )
        return ""

    mime = _mime_for_path(file_path)
    logger.debug("Preparing vision request for %s (mime: %s)", file_path.name, mime)

    with file_path.open("rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    data_url = f"data:{mime};base64,{encoded}"

    try:
        logger.info("Calling Groq Vision API with model: %s", GROQ_VISION_MODEL)
        response = get_groq_client().chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            max_completion_tokens=2048,
        )
        result = (response.choices[0].message.content or "").strip()
        logger.info("Vision API returned %d chars for %s", len(result), file_path.name)
        return result
    except Exception as exc:
        logger.error("Vision API failed for %s: %s", file_path.name, exc)
        return ""


def process_image_upload(file_path: Path) -> tuple[str, dict, str | None]:
    metadata = {"file_type": "image"}
    logger.info("Processing image upload: %s", file_path.name)

    ocr_text = extract_image_text(file_path)
    if len(ocr_text) >= OCR_MIN_CHARS:
        logger.info("Image OCR successful (%d chars), using OCR result", len(ocr_text))
        metadata["extraction"] = "ocr"
        return truncate_text(ocr_text), metadata, "ocr"

    logger.debug("OCR returned insufficient text (%d chars < %d threshold)", len(ocr_text), OCR_MIN_CHARS)

    if file_path.stat().st_size > MAX_IMAGE_BYTES:
        logger.warning("Image too large for vision API: %d bytes", file_path.stat().st_size)
        metadata["extraction"] = "none"
        metadata["error"] = "image_too_large_for_vision"
        return ocr_text, metadata, None

    vision_text = describe_image_with_vision(file_path)
    if vision_text:
        logger.info("Vision API successful (%d chars)", len(vision_text))
        metadata["extraction"] = "vision"
        return truncate_text(vision_text), metadata, "vision"

    logger.warning("Both OCR and Vision failed for %s", file_path.name)
    metadata["extraction"] = "none"
    metadata["error"] = "processing_failed"
    return ocr_text, metadata, None


def extract_plain_text(file_path: Path) -> tuple[str, dict, str | None]:
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = file_path.read_text(encoding="latin-1")
        except Exception:
            return "", {"file_type": "text"}, None

    text = truncate_text(text.strip())
    metadata = {"file_type": "text", "extraction": "plain" if text else "none"}
    return text, metadata, "plain" if text else None
