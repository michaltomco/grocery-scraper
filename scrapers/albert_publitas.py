"""Official Albert Publitas supermarket discovery, independent of the app API.

Publication times only bound discovery. Actual offer dates must come from PDFs.
"""
from datetime import date, datetime, timedelta
import re

import requests

from scrapers.common import today_timestamp
from scrapers.http_retry import request_with_retry

LIST_URL = "https://api.publitas.com/v1/groups/albert/publications.json"
PUBLICATION_BASE = "https://api.publitas.com/v1/groups/albert/publications"
GENERIC_SM = re.compile(r"(?:0[1-9]|[1-4]\d|5[0-3])sm_akcni_letak")


def discover_publications(records: list, today: date) -> list[str]:
    if not isinstance(records, list):
        raise ValueError("Albert publisher listing must be a list")
    selected = {}
    for record in records:
        if not isinstance(record, dict) or not GENERIC_SM.fullmatch(str(record.get("slug", ""))):
            continue
        published = datetime.fromisoformat(record["onlineAt"])
        if published.tzinfo is None:
            raise ValueError("Publisher timestamp has no timezone")
        if today - timedelta(days=14) <= published.date() <= today:
            selected[record["slug"]] = published
    if len(selected) > 8:
        raise ValueError("Too many recent generic supermarket publications")
    return sorted(selected, key=lambda slug: (selected[slug], slug), reverse=True)


def get_json(url: str):
    response = request_with_retry(lambda: requests.get(url, timeout=30), label="Albert Publitas JSON")
    return response.json()


def get_pdf(url: str) -> bytes:
    response = request_with_retry(
        lambda: requests.get(url, timeout=60, stream=True), label="Albert Publitas PDF"
    )
    with response:
        chunks, total = [], 0
        for chunk in response.iter_content(1024 * 1024):
            total += len(chunk)
            if total > 64 * 1024 * 1024:
                raise ValueError("Albert PDF exceeds 64 MiB limit")
            chunks.append(chunk)
    content = b"".join(chunks)
    if not content.startswith(b"%PDF-"):
        raise ValueError("Publisher response is not a PDF")
    return content


def parse_pdf(content: bytes, slug: str, source_url: str) -> list[dict]:
    from scrapers.albert_pdf import extract_pdf_products
    return extract_pdf_products(content, slug, source_url)


def fetch_publitas_products(store_ids: tuple[str, ...], *, today: date | None = None) -> list[dict]:
    # Only this configured branch was verified against Albert's official store
    # applicability list. Never silently substitute generic SM for another store.
    if store_ids != ("583",):
        raise ValueError("Publitas recovery is verified only for supermarket store 583")
    today = today or datetime.fromisoformat(today_timestamp()).date()
    slugs = discover_publications(get_json(LIST_URL), today)
    rows = []
    for slug in slugs:
        metadata = get_json(f"{PUBLICATION_BASE}/{slug}.json")
        config = metadata["config"]
        publication_id = config["publicationId"]
        path = config["downloadPdfUrl"]
        if config.get("slug") != slug or not isinstance(publication_id, int) or not re.fullmatch(
            rf"/90263/{publication_id}/pdfs/[0-9a-f]{{8}}-(?:[0-9a-f]{{4}}-){{3}}[0-9a-f]{{12}}\.pdf", str(path)
        ):
            raise ValueError("Unexpected Albert publication identity or PDF URL")
        url = "https://view.publitas.com" + path
        for row in parse_pdf(get_pdf(url), slug, url):
            start, end = (date.fromisoformat(value) for value in row["date_range"].split(" - "))
            if end < start:
                raise ValueError("Reversed PDF offer dates")
            if end >= today and start <= today + timedelta(days=7):
                rows.append(row)
    if not rows:
        raise ValueError("No verified current/upcoming Albert PDF offers; snapshot preserved")
    print(f"Albert Publitas: {len(rows)} conservatively parsed offers (partial leaflet coverage)")
    return rows
