"""Try HTTP fetch of brochure page and count embedded PDF URLs."""

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import KB_BROCHURE_HUB_URL
from app.services.kb_scraper import extract_pdf_urls_from_html, HTTP_HEADERS

url = KB_BROCHURE_HUB_URL
r = httpx.get(url, headers=HTTP_HEADERS, follow_redirects=True, timeout=90)
print("status", r.status_code, "bytes", len(r.text))
if "access denied" in r.text.lower()[:600]:
    print("BLOCKED by WAF")
else:
    out = ROOT / "data" / "kb" / "brochure-http-fetched.html"
    out.write_text(r.text, encoding="utf-8")
    urls = extract_pdf_urls_from_html(r.text, url)
    print("saved", out)
    print("pdf urls found", len(urls))
    for u in sorted(urls)[:15]:
        print(" ", u)
    if len(urls) > 15:
        print(" ...")
