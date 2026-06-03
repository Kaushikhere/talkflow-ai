import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "talkflow.db"
UPLOADS_DIR = BASE_DIR / "uploads"

OCR_MIN_CHARS = int(os.getenv("OCR_MIN_CHARS", "30"))
GROQ_VISION_MODEL = os.getenv(
    "GROQ_VISION_MODEL",
    "meta-llama/llama-4-scout-17b-16e-instruct",
)
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_TEXT_LENGTH = 50000

_DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
TESSERACT_CMD = os.getenv("TESSERACT_CMD") or (
    str(_DEFAULT_TESSERACT) if _DEFAULT_TESSERACT.is_file() else None
)
