"""Scrape Care Insurance PDFs and ingest into the knowledge base."""

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.kb_ingest import run_kb_pipeline

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape Care PDFs and ingest into Chroma + kb_documents",
    )
    parser.add_argument(
        "--no-scrape",
        action="store_true",
        help="Skip scraping; only ingest PDFs already on disk",
    )
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="Only scrape and download PDFs",
    )
    parser.add_argument(
        "--brochure-only",
        action="store_true",
        help="Only scrape the health-insurance-brochure.html hub (all accordions)",
    )
    parser.add_argument(
        "--brochure-html",
        type=Path,
        help=(
            "Saved brochure page HTML from your browser (when automated scrape is blocked). "
            "In Chrome: open the brochure page, Ctrl+S -> Web Page Complete, pass that .html file."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-index every PDF even if already indexed (refreshes embeddings)",
    )
    args = parser.parse_args()

    brochure_html = args.brochure_html
    if brochure_html and not brochure_html.is_absolute():
        brochure_html = ROOT_DIR / brochure_html

    result = run_kb_pipeline(
        scrape=not args.no_scrape,
        ingest=not args.no_ingest,
        brochure_html_path=brochure_html,
        brochure_only=args.brochure_only,
        force_reindex=args.force,
    )

    print(f"Run ID: {result['run_id']}")
    print(f"Status: {result['status']}")
    print(f"Scrape: {result.get('scrape')}")
    print(f"Documents indexed this run: {result.get('documents_added', 0)}")
    print(f"Chunks added this run: {result.get('chunks_added', 0)}")
    print(f"PDFs processed: {result.get('ingest_count', 0)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
