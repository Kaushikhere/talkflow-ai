from __future__ import annotations

import hashlib
import html as html_lib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import httpx
from playwright.sync_api import sync_playwright

from app.config import (
    CARE_INSURANCE_ORIGIN,
    KB_BROCHURE_HUB_URL,
    KB_CMS_ORIGIN,
    KB_EXTERNAL_DIR,
    KB_MAX_PDFS,
    KB_SCRAPE_BROCHURE_HUB,
    KB_SCRAPE_DELAY_SEC,
    KB_SCRAPE_MAX_DEPTH,
    KB_SCRAPE_MAX_PAGES,
    KB_SCRAPE_PRODUCT_PAGES,
    KB_SEED_URLS,
)

logger = logging.getLogger(__name__)

PDF_RE = re.compile(r"\.pdf(?:$|[?#])", re.IGNORECASE)
PRODUCT_PATH_RE = re.compile(r"/product/", re.IGNORECASE)
HTML_PDF_ABS_RE = re.compile(r"https?://[^\s\"'<>\\]+\.pdf[^\s\"'<>\\]*", re.IGNORECASE)
HTML_PDF_REL_RE = re.compile(r"""['"]([/][^'"]+\.pdf[^'"]*)['"]""", re.IGNORECASE)
HTML_HREF_RE = re.compile(r"""href\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)
CMS_PDF_PATH_RE = re.compile(
    r"(?:https?://cms\.careinsurance\.com)?(/cms/public/uploads/[^\s\"'<>\\]+\.pdf)",
    re.IGNORECASE,
)
# JSON-escaped URLs inside inline scripts
JSON_PDF_RE = re.compile(
    r"https?:\\?/\\?/[^\s\"'\\]+\.pdf[^\s\"'\\]*",
    re.IGNORECASE,
)

ALLOWED_SITE_NETLOC = urlparse(CARE_INSURANCE_ORIGIN).netloc.lower()
CMS_NETLOC = urlparse(KB_CMS_ORIGIN).netloc.lower()
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BROCHURE_EXPAND_SELECTORS = (
    ".panel-title a",
    ".panel-title",
    ".accordion-button",
    "[data-bs-toggle='collapse']",
    "[data-toggle='collapse']",
    ".card-header button",
    ".card-header a",
    ".collapse-title",
    "summary",
)

BROCHURE_TEXT_CLICK_RE = re.compile(
    r"prospectus|brochure|sales\s+literature",
    re.IGNORECASE,
)


@dataclass
class DownloadedPdf:
    url: str
    path: Path
    title: str


@dataclass
class ScrapeResult:
    discovered_urls: list[str]
    downloaded: list[DownloadedPdf]
    pages_visited: int


def normalize_url(url: str, base: str | None = None) -> str:
    raw = html_lib.unescape(url.strip()).replace("\\/", "/")
    if base:
        raw = urljoin(base, raw)
    parsed = urlparse(raw)
    if not parsed.scheme:
        parsed = urlparse(urljoin(CARE_INSURANCE_ORIGIN + "/", raw))
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def is_pdf_url(url: str) -> bool:
    return bool(PDF_RE.search(urlparse(url).path) or PDF_RE.search(url))


def is_allowed_pdf_host(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    if netloc.endswith("careinsurance.com"):
        return True
    return netloc == CMS_NETLOC or netloc.endswith("." + CMS_NETLOC)


def _absolute_pdf_url(candidate: str, page_url: str | None = None) -> str | None:
    normalized = normalize_url(candidate, page_url)
    if not is_pdf_url(normalized):
        return None
    if is_allowed_pdf_host(normalized):
        return normalized
    return None


def is_same_site_page(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    base = ALLOWED_SITE_NETLOC
    allowed = {base, f"www.{base}", CMS_NETLOC}
    if base.startswith("www."):
        allowed.add(base[4:])
    return netloc in allowed


def is_crawlable_page(url: str, seeds: list[str]) -> bool:
    if not is_same_site_page(url) or is_pdf_url(url):
        return False
    path = urlparse(url).path.lower()
    if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".zip", ".pdf")):
        return False
    if normalize_url(url) in {normalize_url(s) for s in seeds}:
        return True
    if PRODUCT_PATH_RE.search(path):
        return KB_SCRAPE_PRODUCT_PAGES
    return False


def _safe_filename(url: str) -> str:
    path_name = Path(urlparse(url).path).name or "document.pdf"
    if not path_name.lower().endswith(".pdf"):
        path_name = f"{path_name}.pdf"
    stem = re.sub(r"[^\w.\-]+", "_", path_name[:-4]).strip("._") or "document"
    digest = hashlib.sha256(url.encode()).hexdigest()[:10]
    return f"{stem}-{digest}.pdf"


def extract_pdf_urls_from_html(html: str, page_url: str) -> set[str]:
    urls: set[str] = set()
    for match in HTML_PDF_ABS_RE.findall(html):
        absolute = _absolute_pdf_url(match, page_url)
        if absolute:
            urls.add(absolute)
    for match in HTML_PDF_REL_RE.findall(html):
        path = unquote(match)
        if path.lower().startswith("/cms/public/"):
            absolute = _absolute_pdf_url(urljoin(KB_CMS_ORIGIN, path))
        else:
            absolute = _absolute_pdf_url(path, page_url)
        if absolute:
            urls.add(absolute)
    for match in CMS_PDF_PATH_RE.findall(html):
        path = unquote(match)
        if not path.startswith("/"):
            path = "/" + path
        absolute = _absolute_pdf_url(urljoin(KB_CMS_ORIGIN, path))
        if absolute:
            urls.add(absolute)
    for match in JSON_PDF_RE.findall(html):
        cleaned = match.replace("\\/", "/").replace("\\u0026", "&")
        absolute = _absolute_pdf_url(cleaned, page_url)
        if absolute:
            urls.add(absolute)
    # Inline JSON arrays/objects that mention download_center PDFs
    for block in re.findall(r"<script[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE):
        if "download_center" not in block.lower() and ".pdf" not in block.lower():
            continue
        for fragment in re.findall(r"https?://[^\s\"'\\]+\.pdf", block, re.IGNORECASE):
            absolute = _absolute_pdf_url(fragment, page_url)
            if absolute:
                urls.add(absolute)
        for path in CMS_PDF_PATH_RE.findall(block):
            absolute = _absolute_pdf_url(urljoin(KB_CMS_ORIGIN, unquote(path)), page_url)
            if absolute:
                urls.add(absolute)
    return urls


def extract_page_links_from_html(html: str, page_url: str) -> set[str]:
    links: set[str] = set()
    for match in HTML_HREF_RE.findall(html):
        candidate = normalize_url(match, page_url)
        if is_same_site_page(candidate):
            links.add(candidate)
    return links


def _apply_pdf_cap(urls: set[str]) -> list[str]:
    ordered = sorted(urls)
    if KB_MAX_PDFS > 0:
        ordered = ordered[:KB_MAX_PDFS]
    return ordered


def _html_is_waf_block(html: str) -> bool:
    sample = html[:1500].lower()
    return "access denied" in sample or "don't have permission" in sample


def _page_blocked(page) -> bool:
    try:
        text = page.inner_text("body", timeout=5000)
    except Exception:
        return False
    return _html_is_waf_block(text)


def _fetch_brochure_html_http(hub_url: str) -> str | None:
    """Plain HTTP fetch; works on some networks when headless Playwright is blocked."""
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=90.0,
            headers=HTTP_HEADERS,
        ) as client:
            response = client.get(hub_url)
            response.raise_for_status()
            html = response.text
    except Exception as exc:
        logger.warning("Brochure HTTP fetch failed: %s", exc)
        return None
    if _html_is_waf_block(html):
        logger.warning("Brochure HTTP fetch blocked by WAF")
        return None
    return html


def _collect_dom_pdf_links(page, page_url: str) -> set[str]:
    urls: set[str] = set()
    urls.update(extract_pdf_urls_from_html(page.content(), page_url))
    try:
        hrefs = page.eval_on_selector_all(
            "a[href]",
            """els => els
                .map(e => e.href)
                .filter(h => h && h.toLowerCase().includes('.pdf'))""",
        )
        for href in hrefs:
            absolute = _absolute_pdf_url(href, page_url)
            if absolute:
                urls.add(absolute)
    except Exception as exc:
        logger.debug("DOM pdf link eval failed: %s", exc)
    return urls


def discover_brochure_hub_pdfs(
    *,
    brochure_url: str | None = None,
    saved_html_path: Path | None = None,
) -> set[str]:
    """
    Discover every PDF linked from the Care brochure download hub.

    - Primary: Playwright expands accordions and collects links + network PDFs.
    - Fallback: parse a saved HTML file (Save Page in browser when WAF blocks bots).
    """
    hub_url = normalize_url(brochure_url or KB_BROCHURE_HUB_URL)
    pdf_urls: set[str] = set()

    if saved_html_path and saved_html_path.is_file():
        html = saved_html_path.read_text(encoding="utf-8", errors="ignore")
        if _html_is_waf_block(html):
            raise ValueError(
                f"Saved HTML is an Access Denied page, not the brochure list: {saved_html_path}. "
                "Open the brochure page in Chrome, expand every product row, then Ctrl+S "
                "and save as 'Webpage, Complete' to data/kb/brochure-saved.html"
            )
        pdf_urls.update(extract_pdf_urls_from_html(html, hub_url))
        logger.info(
            "Loaded %d PDF URLs from saved HTML: %s",
            len(pdf_urls),
            saved_html_path,
        )
        return pdf_urls

    http_html = _fetch_brochure_html_http(hub_url)
    if http_html:
        pdf_urls.update(extract_pdf_urls_from_html(http_html, hub_url))
        logger.info("Brochure HTTP prefetch: %d PDF URLs", len(pdf_urls))

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="en-IN",
        )
        page = context.new_page()

        def on_response(response) -> None:
            try:
                url = normalize_url(response.url)
                content_type = (response.headers.get("content-type") or "").lower()
                if is_allowed_pdf_host(url) and (
                    "pdf" in content_type or is_pdf_url(url)
                ):
                    pdf_urls.add(url)
            except Exception:
                pass

        page.on("response", on_response)

        try:
            page.goto(hub_url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(4000)
        except Exception as exc:
            logger.error("Brochure hub navigation failed: %s", exc)
            browser.close()
            return pdf_urls

        if _page_blocked(page):
            logger.warning("Brochure hub blocked by Playwright/WAF")
            if not pdf_urls and not http_html:
                http_html = _fetch_brochure_html_http(hub_url)
                if http_html:
                    pdf_urls.update(extract_pdf_urls_from_html(http_html, hub_url))
                    logger.info("Brochure HTTP retry: %d PDF URLs", len(pdf_urls))
            if not pdf_urls:
                logger.error(
                    "No brochure PDFs found. Save the page in Chrome (expand all rows first), "
                    "then run: .\\.venv\\Scripts\\python.exe scripts\\run_kb_pipeline.py "
                    "--brochure-html data\\kb\\brochure-saved.html --force"
                )
            browser.close()
            return pdf_urls

        pdf_urls.update(_collect_dom_pdf_links(page, hub_url))

        for _ in range(20):
            page.evaluate("window.scrollBy(0, Math.max(400, window.innerHeight * 0.8))")
            page.wait_for_timeout(350)
        pdf_urls.update(_collect_dom_pdf_links(page, hub_url))

        clicked = 0
        for selector in BROCHURE_EXPAND_SELECTORS:
            loc = page.locator(selector)
            count = loc.count()
            if count == 0:
                continue
            logger.info("Brochure hub: clicking %d x %s", count, selector)
            for index in range(count):
                try:
                    item = loc.nth(index)
                    item.scroll_into_view_if_needed(timeout=8000)
                    if not item.is_visible():
                        continue
                    item.click(timeout=5000)
                    page.wait_for_timeout(450)
                    pdf_urls.update(_collect_dom_pdf_links(page, hub_url))
                    clicked += 1
                except Exception:
                    continue

        try:
            text_rows = page.get_by_text(BROCHURE_TEXT_CLICK_RE)
            row_count = text_rows.count()
            logger.info("Brochure hub: text-row clicks up to %d", row_count)
            for index in range(min(row_count, 120)):
                try:
                    row = text_rows.nth(index)
                    row.scroll_into_view_if_needed(timeout=5000)
                    if not row.is_visible():
                        continue
                    row.click(timeout=3000)
                    page.wait_for_timeout(350)
                    pdf_urls.update(_collect_dom_pdf_links(page, hub_url))
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("Text-row brochure clicks skipped: %s", exc)

        logger.info(
            "Brochure hub Playwright: %d PDF URLs (%d expand clicks)",
            len(pdf_urls),
            clicked,
        )
        browser.close()

    return pdf_urls


def discover_pdf_urls_http(seed_urls: list[str] | None = None) -> tuple[list[str], int]:
    seeds = [normalize_url(u) for u in (seed_urls or KB_SEED_URLS)]
    pdf_urls: set[str] = set()
    visited_pages: set[str] = set()
    queue: list[tuple[str, int]] = [(s, 0) for s in seeds]

    with httpx.Client(
        follow_redirects=True,
        timeout=60.0,
        headers=HTTP_HEADERS,
    ) as client:
        while queue and len(visited_pages) < KB_SCRAPE_MAX_PAGES:
            page_url, depth = queue.pop(0)
            if page_url in visited_pages:
                continue
            if depth > KB_SCRAPE_MAX_DEPTH and page_url not in seeds:
                continue
            if not is_crawlable_page(page_url, seeds) and page_url not in seeds:
                continue

            visited_pages.add(page_url)
            logger.info("Fetching (%d): %s", depth, page_url)

            try:
                response = client.get(page_url)
                response.raise_for_status()
                html = response.text
            except Exception as exc:
                logger.warning("HTTP fetch failed for %s: %s", page_url, exc)
                time.sleep(KB_SCRAPE_DELAY_SEC)
                continue

            if "access denied" in html.lower()[:800]:
                logger.warning("HTTP blocked for %s (WAF)", page_url)
                time.sleep(KB_SCRAPE_DELAY_SEC)
                continue

            pdf_urls.update(extract_pdf_urls_from_html(html, page_url))

            if depth < KB_SCRAPE_MAX_DEPTH:
                for link in extract_page_links_from_html(html, page_url):
                    if is_pdf_url(link):
                        absolute = _absolute_pdf_url(link, page_url)
                        if absolute:
                            pdf_urls.add(absolute)
                        continue
                    if link not in visited_pages and (
                        is_crawlable_page(link, seeds) or link in seeds
                    ):
                        queue.append((link, depth + 1))

            time.sleep(KB_SCRAPE_DELAY_SEC)

    ordered = _apply_pdf_cap(pdf_urls)
    logger.info(
        "HTTP discovery: %d PDFs from %d pages (cap=%s)",
        len(ordered),
        len(visited_pages),
        KB_MAX_PDFS,
    )
    return ordered, len(visited_pages)


def discover_pdf_urls_playwright(seed_urls: list[str] | None = None) -> set[str]:
    """Supplement: capture PDF URLs from network responses on seed pages."""
    seeds = [normalize_url(u) for u in (seed_urls or KB_SEED_URLS)]
    pdf_urls: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="en-IN",
        )
        page = context.new_page()

        def on_response(response) -> None:
            try:
                url = normalize_url(response.url)
                content_type = (response.headers.get("content-type") or "").lower()
                if is_allowed_pdf_host(url) and (
                    "pdf" in content_type or is_pdf_url(url)
                ):
                    pdf_urls.add(url)
            except Exception:
                pass

        page.on("response", on_response)

        for seed in seeds:
            if normalize_url(seed) == normalize_url(KB_BROCHURE_HUB_URL):
                continue
            try:
                page.goto(seed, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                if _page_blocked(page):
                    logger.warning("Playwright blocked for %s", seed)
                    continue
                html = page.content()
                if len(html) > 1000:
                    pdf_urls.update(extract_pdf_urls_from_html(html, seed))
            except Exception as exc:
                logger.warning("Playwright seed fetch failed for %s: %s", seed, exc)
            time.sleep(KB_SCRAPE_DELAY_SEC)

        browser.close()

    return pdf_urls


def discover_pdf_urls(
    seed_urls: list[str] | None = None,
    *,
    brochure_html_path: Path | None = None,
    brochure_only: bool = False,
) -> tuple[list[str], int]:
    merged: set[str] = set()
    pages_visited = 0

    if KB_SCRAPE_BROCHURE_HUB or brochure_html_path:
        merged.update(
            discover_brochure_hub_pdfs(saved_html_path=brochure_html_path)
        )

    if not brochure_only:
        http_urls, pages_visited = discover_pdf_urls_http(seed_urls)
        merged.update(http_urls)
        try:
            merged.update(discover_pdf_urls_playwright(seed_urls))
        except Exception as exc:
            logger.warning("Playwright supplement skipped: %s", exc)

    ordered = _apply_pdf_cap(merged)
    logger.info("Total discovered PDF URLs: %d", len(ordered))
    return ordered, pages_visited


def download_pdfs(
    pdf_urls: list[str],
    *,
    dest_dir: Path | None = None,
) -> list[DownloadedPdf]:
    target_dir = dest_dir or KB_EXTERNAL_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[DownloadedPdf] = []

    with httpx.Client(
        follow_redirects=True,
        timeout=120.0,
        headers=HTTP_HEADERS,
    ) as client:
        for index, url in enumerate(pdf_urls):
            filename = _safe_filename(url)
            dest_path = target_dir / filename
            if dest_path.is_file() and dest_path.stat().st_size > 0:
                logger.info("Already downloaded: %s", filename)
                title = Path(urlparse(url).path).stem.replace("-", " ").replace("_", " ")
                downloaded.append(DownloadedPdf(url=url, path=dest_path, title=title))
                time.sleep(KB_SCRAPE_DELAY_SEC)
                continue

            logger.info("Downloading (%d/%d): %s", index + 1, len(pdf_urls), url)
            try:
                response = client.get(url)
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").lower()
                body = response.content
                if not body.startswith(b"%PDF") and "pdf" not in content_type:
                    logger.warning("Skipping non-PDF response: %s", url)
                    continue
                dest_path.write_bytes(body)
                title = Path(urlparse(url).path).stem.replace("-", " ").replace("_", " ")
                downloaded.append(DownloadedPdf(url=url, path=dest_path, title=title))
            except Exception as exc:
                logger.error("Download failed for %s: %s", url, exc)

            time.sleep(KB_SCRAPE_DELAY_SEC)

    return downloaded


def scrape_care_pdfs(
    seed_urls: list[str] | None = None,
    *,
    brochure_html_path: Path | None = None,
    brochure_only: bool = False,
) -> ScrapeResult:
    KB_EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    urls, pages_visited = discover_pdf_urls(
        seed_urls,
        brochure_html_path=brochure_html_path,
        brochure_only=brochure_only,
    )
    files = download_pdfs(urls)
    return ScrapeResult(
        discovered_urls=urls,
        downloaded=files,
        pages_visited=pages_visited,
    )
