"""
Remove byte-identical duplicate PDFs under data/kb/external/.
Keeps the cleanest filename per group (no hash suffix, shortest name).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_DIR = ROOT / "data" / "kb" / "external"
DB_PATH = ROOT / "talkflow.db"
HASH_SUFFIX = re.compile(r"-[a-f0-9]{8,12}\.pdf$", re.I)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def keeper_score(path: Path) -> tuple:
    name = path.name
    has_hash_suffix = bool(HASH_SUFFIX.search(name))
    return (has_hash_suffix, len(name), name)


def rel_raw(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def main(dry_run: bool = False) -> int:
    if not EXTERNAL_DIR.is_dir():
        print(f"Missing directory: {EXTERNAL_DIR}")
        return 1

    pdfs = sorted(EXTERNAL_DIR.glob("*.pdf"))
    by_hash: dict[str, list[Path]] = defaultdict(list)
    for p in pdfs:
        by_hash[file_sha256(p)].append(p)

    to_delete: list[Path] = []
    keepers: list[Path] = []
    for paths in by_hash.values():
        if len(paths) == 1:
            keepers.append(paths[0])
            continue
        keep = min(paths, key=keeper_score)
        keepers.append(keep)
        for p in paths:
            if p != keep:
                to_delete.append(p)

    print(f"PDFs scanned: {len(pdfs)}")
    print(f"Unique content: {len(by_hash)}")
    print(f"Keeping: {len(keepers)}")
    print(f"Deleting: {len(to_delete)}")
    for p in sorted(to_delete):
        print(f"  - {p.name}")

    if dry_run:
        print("\n(dry run — no files removed)")
        return 0

    deleted_rels: set[str] = set()
    for p in to_delete:
        rel = rel_raw(p)
        p.unlink()
        deleted_rels.add(rel)
        print(f"Deleted {p.name}")

    if DB_PATH.exists() and deleted_rels:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        removed = 0
        for rel in deleted_rels:
            cur.execute("DELETE FROM kb_documents WHERE raw_path = ?", (rel,))
            removed += cur.rowcount
            # Windows paths may be stored with backslashes
            alt = rel.replace("/", "\\")
            if alt != rel:
                cur.execute("DELETE FROM kb_documents WHERE raw_path = ?", (alt,))
                removed += cur.rowcount
        conn.commit()
        conn.close()
        print(f"Removed {removed} kb_documents row(s) for deleted files")

    print("\nDone. Re-run ingest if you want Chroma aligned:")
    print("  .\\.venv\\Scripts\\python.exe scripts\\run_kb_pipeline.py --no-scrape")
    return 0


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    raise SystemExit(main(dry_run=dry))
