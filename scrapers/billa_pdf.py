"""Disabled BILLA PDF experiment; not an offer source.

The old fixed-PDF/date/layout scaffold was unsafe and has been removed.
Current PDF discovery is exposed separately for source investigation only;
no PDF-to-offer parser is approved. Both historic entry points fail closed.
"""
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from scrapers.billa import BillaScrapeError, CONFIG, main


def discover_pdf_urls(official_html: str) -> list[str]:
    """Inspect BILLA's official HTML; discovery alone never validates offers.

    Only explicit HTTPS Publitas PDF download anchors are returned, in document
    order. The caller must obtain this HTML from https://www.billa.cz/letaky-billa.
    Publication IDs, filenames and dates are deliberately not pinned.
    """
    urls = []
    for anchor in BeautifulSoup(official_html, "html.parser").find_all("a", href=True):
        url = anchor["href"]
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.netloc == "view.publitas.com"
                and "/pdfs/" in parsed.path and parsed.path.endswith(".pdf")):
            if url not in urls:
                urls.append(url)
    return urls


def run_billa_scrape() -> list[dict]:
    main()
    raise AssertionError("Disabled BILLA refresh unexpectedly returned")


if __name__ == "__main__":
    main()
