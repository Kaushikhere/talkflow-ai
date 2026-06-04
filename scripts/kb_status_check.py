import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

kb = ROOT / "data" / "kb"
external = kb / "external"
pdfs = sorted(kb.glob("*.pdf")) + sorted(external.glob("*.pdf"))
print(f"PDF files on disk: {len(pdfs)}")
print(f"  data/kb/: {len(list(kb.glob('*.pdf')))}")
print(f"  data/kb/external/: {len(list(external.glob('*.pdf')))}")

conn = sqlite3.connect(ROOT / "talkflow.db")
row = conn.execute(
    """
    SELECT COUNT(*) AS total,
           SUM(CASE WHEN status = 'indexed' THEN 1 ELSE 0 END) AS indexed
    FROM kb_documents
    """
).fetchone()
print(f"kb_documents: {row[0]} total, {row[1]} indexed")

products = [
    "care supreme",
    "protect plus",
    "explore advantage",
    "care saksham",
    "instant care",
    "senior health",
    "secure plus",
    "special care",
    "arogya",
    "assure",
]
titles = [r[0].lower() for r in conn.execute("SELECT title FROM kb_documents WHERE status = 'indexed'")]
print("\nProduct coverage (indexed titles):")
for p in products:
    hit = any(p in t for t in titles)
    print(f"  {p}: {'YES' if hit else 'NO'}")

conn.close()
