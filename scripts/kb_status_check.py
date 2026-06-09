import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.kb_embeddings import chroma_chunk_count, chroma_has_document_chunks

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
           SUM(CASE WHEN status = 'indexed' THEN 1 ELSE 0 END) AS indexed,
           SUM(CASE WHEN status = 'indexed' THEN chunk_count ELSE 0 END) AS chunks_sqlite
    FROM kb_documents
    """
).fetchone()
print(f"kb_documents: {row[0]} total, {row[1]} indexed")
print(f"SQLite chunk_count sum: {row[2] or 0}")

try:
    chroma_total = chroma_chunk_count()
    print(f"Chroma vectors: {chroma_total}")
except Exception as exc:
    print(f"Chroma vectors: unavailable ({exc})")
    chroma_total = None

indexed_ids = [
    r[0]
    for r in conn.execute(
        "SELECT id FROM kb_documents WHERE status = 'indexed'"
    ).fetchall()
]
drift = sum(1 for doc_id in indexed_ids if not chroma_has_document_chunks(doc_id))
print(f"Indexed docs missing Chroma chunks: {drift}")

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
titles = [
    r[0].lower()
    for r in conn.execute("SELECT title FROM kb_documents WHERE status = 'indexed'")
]
print("\nProduct coverage (indexed titles):")
for p in products:
    hit = any(p in t for t in titles)
    print(f"  {p}: {'YES' if hit else 'NO'}")

conn.close()

if chroma_total is not None and drift:
    print("\nWARN: SQLite/Chroma drift detected — run re-index or dedupe scripts.")
    raise SystemExit(1)
