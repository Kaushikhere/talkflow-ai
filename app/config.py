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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


KB_ENABLED = _env_bool("KB_ENABLED", False)
KB_TOP_K = int(os.getenv("KB_TOP_K", "12"))
MAX_CHAT_HISTORY = int(os.getenv("MAX_CHAT_HISTORY", "20"))
KB_CHUNK_SIZE = int(os.getenv("KB_CHUNK_SIZE", "800"))
KB_DATA_DIR = BASE_DIR / "data" / "kb"
KB_EXTERNAL_DIR = KB_DATA_DIR / "external"
CHROMA_PATH = BASE_DIR / "data" / "chroma"

CARE_INSURANCE_ORIGIN = os.getenv(
    "CARE_INSURANCE_ORIGIN",
    "https://www.careinsurance.com",
).rstrip("/")

_DEFAULT_SEED_PATHS = (
    "/health-insurance-brochure.html",
    "/other-downloads.html",
)
_seed_raw = os.getenv("KB_SEED_URLS", "")
if _seed_raw.strip():
    KB_SEED_URLS = [u.strip() for u in _seed_raw.split(",") if u.strip()]
else:
    KB_SEED_URLS = [f"{CARE_INSURANCE_ORIGIN}{path}" for path in _DEFAULT_SEED_PATHS]

KB_DEFAULT_SOURCE_URL = os.getenv(
    "KB_DEFAULT_SOURCE_URL",
    f"{CARE_INSURANCE_ORIGIN}/health-insurance-brochure.html",
)

# 0 = no cap (ingest every PDF discovered from the brochure hub)
KB_MAX_PDFS = int(os.getenv("KB_MAX_PDFS", "0"))
KB_SCRAPE_DELAY_SEC = float(os.getenv("KB_SCRAPE_DELAY_SEC", "0.8"))
KB_SCRAPE_MAX_DEPTH = int(os.getenv("KB_SCRAPE_MAX_DEPTH", "2"))
KB_SCRAPE_MAX_PAGES = int(os.getenv("KB_SCRAPE_MAX_PAGES", "40"))
KB_SCRAPE_PRODUCT_PAGES = _env_bool("KB_SCRAPE_PRODUCT_PAGES", True)
KB_SCRAPE_BROCHURE_HUB = _env_bool("KB_SCRAPE_BROCHURE_HUB", True)
KB_BROCHURE_HUB_URL = os.getenv(
    "KB_BROCHURE_HUB_URL",
    f"{CARE_INSURANCE_ORIGIN}/health-insurance-brochure.html",
)
KB_CMS_ORIGIN = os.getenv(
    "KB_CMS_ORIGIN",
    "https://cms.careinsurance.com",
).rstrip("/")
