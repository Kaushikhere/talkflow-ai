"""Re-embed all PDFs into Chroma (fixes empty/wiped vector index while SQLite still says indexed)."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.kb_embeddings import COLLECTION_NAME, reset_collection
from app.services.kb_ingest import list_kb_pdf_paths, ingest_pdf_from_path


def main() -> int:
    print(f"Resetting Chroma collection {COLLECTION_NAME}...")
    reset_collection(COLLECTION_NAME)
    paths = list_kb_pdf_paths()
    print(f"Re-indexing {len(paths)} PDF(s) into Chroma...")
    indexed = 0
    skipped = 0
    for pdf_path in paths:
        result = ingest_pdf_from_path(pdf_path)
        status = result.get("status")
        print(f"  {status}: {pdf_path.name} ({result.get('chunk_count', 0)} chunks)")
        if status == "indexed":
            indexed += 1
        elif status == "skipped":
            skipped += 1
    print(f"Done. indexed={indexed} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
