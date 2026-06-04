"""CLI entry point for Care external KB ingest (Step 4)."""

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
        description="Ingest external Care PDFs into TalkFlow KB",
    )
    parser.add_argument(
        "--scrape",
        action="store_true",
        help="Scrape Care website before ingest (default: only files on disk)",
    )
    parser.add_argument("--force", action="store_true", help="Re-index all PDFs")
    parser.add_argument("--brochure-only", action="store_true")
    parser.add_argument("--brochure-html", type=Path)
    args = parser.parse_args()

    brochure_html = args.brochure_html
    if brochure_html and not brochure_html.is_absolute():
        brochure_html = ROOT_DIR / brochure_html

    result = run_kb_pipeline(
        scrape=args.scrape,
        ingest=True,
        brochure_only=args.brochure_only,
        brochure_html_path=brochure_html,
        force_reindex=args.force,
    )

    print(f"Run ID: {result['run_id']}")
    print(f"Status: {result['status']}")
    print(f"Documents indexed: {result.get('documents_added', 0)}")
    print(f"Chunks added: {result.get('chunks_added', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
