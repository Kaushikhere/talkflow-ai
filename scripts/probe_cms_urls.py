"""Probe Care CMS / download pages for PDF URLs."""

import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

URLS = [
    "https://www.careinsurance.com/other-downloads.html",
    "https://www.careinsurance.com/health-insurance-brochure.html",
]

PDF_RE = re.compile(r"https?://[^\s\"'<>\\]+\.pdf[^\s\"'<>\\]*", re.I)
CMS_PDF_RE = re.compile(
    r"(?:https?://cms\.careinsurance\.com)?/?cms/public/uploads/[^\s\"'<>\\]+\.pdf[^\s\"'<>\\]*",
    re.I,
)


def main() -> None:
    client = httpx.Client(
        follow_redirects=True,
        timeout=60.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    for url in URLS:
        try:
            r = client.get(url)
            text = r.text
            print(url, r.status_code, len(text))
            if "Access Denied" in text[:500]:
                print("  BLOCKED")
                continue
            abs_pdfs = set(PDF_RE.findall(text))
            cms = set(CMS_PDF_RE.findall(text))
            print("  abs pdfs:", len(abs_pdfs))
            print("  cms paths:", len(cms))
            for u in sorted(abs_pdfs | cms)[:8]:
                print("   ", u[:100])
        except Exception as exc:
            print(url, "ERR", exc)
    client.close()


if __name__ == "__main__":
    main()
