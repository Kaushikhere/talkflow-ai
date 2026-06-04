"""Index a local PDF into the knowledge base (Chroma + kb_documents)."""

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import KB_DATA_DIR, KB_DEFAULT_SOURCE_URL
from app.services.kb_embeddings import query_chunks
from app.services.kb_ingest import ingest_pdf_from_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a PDF into the TalkFlow KB")
    parser.add_argument(
        "pdf",
        type=Path,
        help="Path to PDF (e.g. data/kb/special-care----prospectus-cum-sales-literature.pdf)",
    )
    parser.add_argument("--title", help="Document title for metadata")
    parser.add_argument(
        "--source-url",
        help="Unique document key (default: file://relative/path)",
    )
    parser.add_argument(
        "--brochure-url",
        default=KB_DEFAULT_SOURCE_URL,
        help="Care brochure catalog URL stored on each chunk",
    )
    parser.add_argument(
        "--query",
        help="Optional search query to run after ingest",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-index even if this PDF was already indexed",
    )
    args = parser.parse_args()

    pdf_path = args.pdf
    if not pdf_path.is_absolute():
        pdf_path = ROOT_DIR / pdf_path

    print(f"Ingesting: {pdf_path}")
    result = ingest_pdf_from_path(
        pdf_path,
        title=args.title,
        source_url=args.source_url,
        brochure_source_url=args.brochure_url,
        force=args.force,
    )
    print(f"Status: {result['status']}")
    print(f"Document ID: {result['document_id']}")
    print(f"Title: {result['title']}")
    print(f"Chunks: {result.get('chunk_count', 0)}")
    print(f"Pages: {result.get('page_count', 'n/a')}")

    if args.query:
        print(f"\nSearch: {args.query!r}")
        hits = query_chunks(args.query, top_k=3)
        for i, hit in enumerate(hits, 1):
            meta = hit.get("metadata") or {}
            print(f"\n--- Hit {i} (distance {hit.get('distance')}) ---")
            print(f"Page: {meta.get('page_number')}  Title: {meta.get('title')}")
            print((hit.get("text") or "")[:400])

    return 0


if __name__ == "__main__":
    sys.exit(main())
