import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.kb_scraper import extract_pdf_urls_from_html

html = """
<a href="/cms/public/uploads/download_center/care-supreme---brochure.pdf">Care Supreme</a>
<script>{"url":"https://cms.careinsurance.com/cms/public/uploads/download_center/protect-plus.pdf"}</script>
"""
urls = extract_pdf_urls_from_html(
    html, "https://www.careinsurance.com/health-insurance-brochure.html"
)
assert len(urls) >= 2, urls
print("OK", urls)
