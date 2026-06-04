"""One-off probe for Care brochure hub DOM (dev only)."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

URL = "https://www.careinsurance.com/health-insurance-brochure.html"
OUT = ROOT / "data" / "kb" / "_brochure_probe.html"


def main() -> None:
    network_pdfs: set[str] = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        def on_response(response) -> None:
            url = response.url
            if ".pdf" in url.lower() or "pdf" in (
                response.headers.get("content-type") or ""
            ).lower():
                network_pdfs.add(url)

        page.on("response", on_response)

        try:
            page.goto(URL, wait_until="networkidle", timeout=120000)
        except Exception as exc:
            print("goto warning:", exc)
        page.wait_for_timeout(8000)

        body_text = page.inner_text("body")[:2000]
        print("body sample:", body_text.replace("\n", " ")[:500])

        html = page.content()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(html, encoding="utf-8")
        print("saved html:", OUT, "len", len(html))

        for pat in [r"\.pdf", r"download", r"brochure", r"care.?supreme", r"data-href", r"data-url"]:
            hits = re.findall(pat, html, re.I)
            print(pat, "count", len(hits))

        # iframes
        frames = page.frames
        print("frames:", len(frames))
        for f in frames:
            try:
                fh = len(f.content())
                print(" frame", f.url[:80], "html", fh)
            except Exception:
                pass

        print("network pdfs:", len(network_pdfs))
        for u in sorted(network_pdfs)[:10]:
            print(" ", u)

        browser.close()


if __name__ == "__main__":
    main()
