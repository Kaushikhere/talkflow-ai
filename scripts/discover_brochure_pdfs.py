"""List PDF URLs from the Care brochure hub (no download)."""

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.kb_scraper import discover_brochure_hub_pdfs, discover_pdf_urls

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover PDF URLs from Care brochure hub",
    )
    parser.add_argument(
        "--brochure-html",
        type=Path,
        help="Saved brochure page HTML from browser",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also run HTTP/Playwright seeds (other-downloads, etc.)",
    )
    args = parser.parse_args()

    brochure_html = args.brochure_html
    if brochure_html and not brochure_html.is_absolute():
        brochure_html = ROOT_DIR / brochure_html

    if args.full:
        urls, pages = discover_pdf_urls(brochure_html_path=brochure_html)
        print(f"Pages visited: {pages}")
    else:
        found = discover_brochure_hub_pdfs(saved_html_path=brochure_html)
        urls = sorted(found)

    print(f"Discovered {len(urls)} PDF URL(s)")
    for url in urls:
        print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
