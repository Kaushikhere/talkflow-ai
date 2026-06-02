from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "talkflow.db"
UPLOADS_DIR = BASE_DIR / "uploads"
