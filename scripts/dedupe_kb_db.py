"""
Deduplicate KB documents at the database + Chroma level.

Groups all indexed kb_documents by content_hash and keeps only the one with the
cleanest title (no hex-hash suffix, shortest name). Duplicate rows and their
Chroma chunk embeddings are deleted.

Usage:
    .venv/Scripts/python.exe scripts/dedupe_kb_db.py            # live run
    .venv/Scripts/python.exe scripts/dedupe_kb_db.py --dry-run  # preview only
"""
from __future__ import annotations

import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "talkflow.db"

HASH_SUFFIX_RE = re.compile(r"[\s_-][a-f0-9]{6,12}$", re.IGNORECASE)
NOISE_PHRASES = (
    " prospectus cum sales literature",
    " policy terms conditions",
    " policy terms and conditions",
    " brochure",
    " prospectus",
    " health insurance product",
    " travel insurance product",
    " personal accident product",
    " insurance product",
)


def _clean_title(raw: str) -> str:
    t = HASH_SUFFIX_RE.sub("", raw.strip()).strip()
    t = re.sub(r"\s*\([^)]*product[^)]*\)", "", t, flags=re.IGNORECASE).strip()
    lower = t.lower()
    for phrase in NOISE_PHRASES:
        if lower.endswith(phrase):
            t = t[: len(t) - len(phrase)].strip()
            lower = t.lower()
    if lower.startswith("add on "):
        t = t[7:].strip()
    return t.lower()


def _keeper_score(row: dict) -> tuple:
    """Lower score = better keeper (no hash suffix, shorter title)."""
    title = row["title"] or ""
    has_hash = bool(HASH_SUFFIX_RE.search(title))
    return (has_hash, len(title), title)


def _chroma_delete(doc_ids: list[int]) -> int:
    """Delete Chroma chunks for the given document IDs. Returns deleted count."""
    try:
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        chroma_path = ROOT / "data" / "chroma"
        client = chromadb.PersistentClient(path=str(chroma_path))
        ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        col = client.get_or_create_collection("talkflow_kb", embedding_function=ef)
        deleted = 0
        for doc_id in doc_ids:
            existing = col.get(where={"document_id": doc_id}, limit=1)
            if existing.get("ids"):
                col.delete(where={"document_id": doc_id})
                deleted += 1
        return deleted
    except Exception as exc:
        print(f"  [warn] Chroma delete skipped: {exc}")
        return 0


def main(dry_run: bool = False) -> int:
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, content_hash, raw_path, status FROM kb_documents ORDER BY id"
    ).fetchall()
    rows = [dict(r) for r in rows]

    # Group 1: by content_hash (truly identical PDFs re-ingested under different names)
    by_hash: dict[str, list[dict]] = defaultdict(list)
    no_hash: list[dict] = []
    for row in rows:
        if row["content_hash"]:
            by_hash[row["content_hash"]].append(row)
        else:
            no_hash.append(row)

    # Group 2: by cleaned title (same product, slightly different name variants)
    by_clean_title: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = _clean_title(row["title"] or "")
        if key:
            by_clean_title[key].append(row)

    to_delete: set[int] = set()

    # Same content_hash → identical PDFs indexed under different filenames
    for h, group in by_hash.items():
        if len(group) < 2:
            continue
        keeper = min(group, key=_keeper_score)
        dupes = [r for r in group if r["id"] != keeper["id"]]
        for d in dupes:
            to_delete.add(d["id"])
        print(
            f"[dup] keep id={keeper['id']} '{keeper['title']}'"
        )
        for d in dupes:
            print(f"      drop id={d['id']} '{d['title']}'")

    # Same cleaned title AND same content_hash group already handled above.
    # We do NOT delete documents that merely share a product name but have
    # different content (e.g. brochure vs prospectus for the same product).
    # Instead we find titles that after cleaning still look like hash-variants:
    # e.g. "arogya sanjeevani policy ...8efd0191" vs "arogya sanjeevani policy ...951dccaa"
    # These have different content_hash values but identical cleaned titles because
    # only the trailing hash differs. Identify them and keep the lowest id.
    for clean, group in by_clean_title.items():
        if len(group) < 2:
            continue
        # Only treat as duplicates when ALL titles in the group are hash-variant
        # filenames (i.e. the raw title ends with a hex suffix before cleaning).
        remaining = [r for r in group if r["id"] not in to_delete]
        if len(remaining) < 2:
            continue
        all_have_hash = all(
            bool(HASH_SUFFIX_RE.search(r["title"] or "")) for r in remaining
        )
        if not all_have_hash:
            continue
        keeper = min(remaining, key=lambda r: r["id"])
        dupes = [r for r in remaining if r["id"] != keeper["id"]]
        for d in dupes:
            to_delete.add(d["id"])
        print(f"[hash-name-dup '{clean}'] keep id={keeper['id']} '{keeper['title']}'")
        for d in dupes:
            print(f"      drop id={d['id']} '{d['title']}'")

    if not to_delete:
        print("No duplicates found — KB is already clean.")
        conn.close()
        return 0

    print(f"\nTotal duplicate rows to remove: {len(to_delete)}")

    if dry_run:
        print("(dry run — no changes made)")
        conn.close()
        return 0

    drop_rows = [r for r in rows if r["id"] in to_delete]
    print("Deleting Chroma chunks for duplicate docs...")
    chroma_deleted = _chroma_delete(list(to_delete))
    print(f"  Chroma collections cleared: {chroma_deleted}")

    files_removed = 0
    for row in drop_rows:
        raw = row.get("raw_path")
        if not raw:
            continue
        pdf_path = (ROOT / raw).resolve()
        try:
            pdf_path.relative_to((ROOT / "data" / "kb").resolve())
        except ValueError:
            continue
        if pdf_path.is_file():
            pdf_path.unlink()
            files_removed += 1
            print(f"  Deleted file {pdf_path.name}")

    print("Removing duplicate rows from kb_documents...")
    placeholders = ",".join("?" * len(to_delete))
    conn.execute(
        f"DELETE FROM kb_documents WHERE id IN ({placeholders})",
        list(to_delete),
    )
    conn.commit()
    print(f"  Removed {len(to_delete)} rows ({files_removed} PDF file(s) deleted)")

    conn.close()
    print("\nDone. No re-index needed — kept documents still have their Chroma chunks.")
    return 0


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    raise SystemExit(main(dry_run=dry))
