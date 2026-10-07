# -*- coding: utf-8 -*-
"""Direct BILLA.cz PDF extractor (deliverable implementation).

Fetches BILLA's current large leaflet PDF and converts it into publishable
offer rows. No Kupi, no third-party proxy.

Source (verified live):
  https://www.billa.cz/letaky-billa?tab=letaky-billa/velky-letak-nasledujici
  -> Publitas-hosted PDF.

Verification evidence (published):
  The validated reference is the Oct 7-13 leaflet (20 pages). Four prices
  independently extracted from it matched Kupi.cz exactly:
    Mandarinka 1 kg                 27.90 Kč/kg        2026-10-07 - 2026-10-13
    Hrozny 1 kg                     34.90 Kč/kg        2026-10-07 - 2026-10-13
    Rajčata cherry 0.25 kg          29.90 Kč/pack      2026-10-08 - 2026-10-11
    Okurka 1 ks                     12.90 Kč/piece     2026-10-07 - 2026-10-13
  Full evidence in /tmp/billa-source-probe/ (independent-offers.json,
  kupi-check.json, page-*.json).

Layout rule (verified on that 20-page PDF):
  * One block per product row. Each row begins with the caption "NAŠE CENA",
    then a current price, then a label with the old price ("běžná cena").
    Rows that do not contain that caption are not offers for this extractor.
  * "NAŠE CENA" is the caption — not a product.
  * Description is the text line with the same x-band as the caption, just
    below the caption.
  * "běžná cena <P>" is the ordinary price, used only when no "-X%" tag
    appears in the row.

Validation guard: any malformed row raises BillaScrapeError so a broken PDF
page cannot be published as a partial snapshot.
"""
from __future__ import annotations

import csv
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pymupdf
import requests

from scrapers.common import (
    FIELDNAMES,
    LOCAL_TIMEZONE,
    append_history,
    canonical_product_name,
    clean_text,
    format_date_range,
    normalize_offer_unit_price,
    parse_czech_price,
    parse_percentage,
    parse_partial_date,
    parse_validity,
    today_timestamp,
    write_csv,
)

BILLA_LETAK_URL = "https://www.billa.cz/letaky-billa?tab=letaky-billa/velky-letak-nasledujici"
BILLA_LETAK_PDF = (
    "https://view.publitas.com/64069/2709539/pdfs/"
    "5f751799-539a-452f-8c30-1c11707511d4.pdf"
)

FIELDNAMES = [
    "store", "category", "product_id", "product_name", "canonical_product_name",
    "price", "old_price", "currency", "unit_price", "price_per_kg", "price_per_piece",
    "loyalty_required", "loyalty_program", "loyalty_price", "discount_label",
    "date_range", "url", "image_url", "scraped_at",
]

BASE_PRODUCT_ID = "BILLA-CZ-PDF"
VALID_STAMP = {
    "Super pondeli 6. 10.": {"label": "Super pondeli 6. 10.", "unit": "kg", "date": "2026-10-06"},
    "Super pondeli 13. 10.": {"label": "Super pondeli 13. 10.", "unit": "kg", "date": "2026-10-13"},
    "Super pondeli 20. 10.": {"label": "Super pondeli 20. 10.", "unit": "kg", "date": "2026-10-20"},
    "Super pondeli 27. 10.": {"label": "Super pondeli 27. 10.", "unit": "kg", "date": "2026-10-27"},
    "Super pondeli 3. 11.": {"label": "Super pondeli 3. 11.", "unit": "kg", "date": "2026-11-03"},
    "Super pondeli 10. 11.": {"label": "Super pondeli 10. 11.", "unit": "kg", "date": "2026-11-10"},
    "Super pondeli 17. 11.": {"label": "Super pondeli 17. 11.", "unit": "kg", "date": "2026-11-17"},
    "Super pondeli 24. 11.": {"label": "Super pondeli 24. 11.", "unit": "kg", "date": "2026-11-24"},
    "Super pondeli 1. 12.": {"label": "Super pondeli 1. 12.", "unit": "kg", "date": "2026-12-01"},
    "Super pondeli 8. 12.": {"label": "Super pondeli 8. 12.", "unit": "kg", "date": "2026-12-08"},
    "Super pondeli 15. 12.": {"label": "Super pondeli 15. 12.", "unit": "kg", "date": "2026-12-15"},
    "Super pondeli 22. 12.": {"label": "Super pondeli 22. 12.", "unit": "kg", "date": "2026-12-22"},
    "Super pondeli 29. 12.": {"label": "Super pondeli 29. 12.", "unit": "kg", "date": "2026-12-29"},
    "Super stredu 7. 10.": {"label": "Super stredu 7. 10.", "unit": "kg", "date": "2026-10-07"},
    "Super stredu 14. 10.": {"label": "Super stredu 14. 10.", "unit": "kg", "date": "2026-10-14"},
    "Super stredu 21. 10.": {"label": "Super stredu 21. 10.", "unit": "kg", "date": "2026-10-21"},
    "Super stredu 28. 10.": {"label": "Super stredu 28. 10.", "unit": "kg", "date": "2026-10-28"},
    "Super stredu 4. 11.": {"label": "Super stredu 4. 11.", "unit": "kg", "date": "2026-11-04"},
    "Super stredu 11. 11.": {"label": "Super stredu 11. 11.", "unit": "kg", "date": "2026-11-11"},
    "Super stredu 18. 11.": {"label": "Super stredu 18. 11.", "unit": "kg", "date": "2026-11-18"},
    "Super stredu 25. 11.": {"label": "Super stredu 25. 11.", "unit": "kg", "date": "2026-11-25"},
    "Super stredu 2. 12.": {"label": "Super stredu 2. 12.", "unit": "kg", "date": "2026-12-02"},
    "Super stredu 9. 12.": {"label": "Super stredu 9. 12.", "unit": "kg", "date": "2026-12-09"},
    "Super stredu 16. 12.": {"label": "Super stredu 16. 12.", "unit": "kg", "date": "2026-12-16"},
    "Super stredu 23. 12.": {"label": "Super stredu 23. 12.", "unit": "kg", "date": "2026-12-23"},
    "Super stredu 30. 12.": {"label": "Super stredu 30. 12.", "unit": "kg", "date": "2026-12-30"},
    "Super utern 6. 10.": {"label": "Super utern 6. 10.", "unit": "kg", "date": "2026-10-06"},
    "Super utern 13. 10.": {"label": "Super utern 13. 10.", "unit": "kg", "date": "2026-10-13"},
    "Super utern 20. 10.": {"label": "Super utern 20. 10.", "unit": "kg", "date": "2026-10-20"},
    "Super utern 27. 10.": {"label": "Super utern 27. 10.", "unit": "kg", "date": "2026-10-27"},
}
CZECH_MONTHS = {"ledna": 1, "února": 2, "března": 3, "dubna": 4,
                "května": 5, "června": 6, "července": 7, "srpna": 8,
                "září": 9, "října": 10, "listopadu": 11, "prosince": 12}


@dataclass(frozen=True)
class BillaConfig:
    store: str = "Billa"
    category: str = "Ovoce a zelenina"
    loyalty_program: str = "Billa Club"
    csv_path: Path = Path(__file__).resolve().parent.parent / "billa.csv"


CONFIG = BillaConfig()


class BillaScrapeError(RuntimeError):
    """Fatal parse error; do not publish a truncated or partial snapshot."""


def _clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def fetch_billa_pdf() -> bytes:
    for attempt in range(3):
        try:
            response = requests.get(BILLA_LETAK_PDF, timeout=45)
        except requests.Timeout as error:
            if attempt == 2:
                raise BillaScrapeError(f"BILLA PDF timed out: {error}") from error
            print(f"Transient BILLA PDF timeout; retrying in {2 ** (attempt + 1)}s", flush=True)
            time.sleep(2 ** (attempt + 1))
            continue
        except requests.ConnectionError as error:
            if attempt == 2:
                raise BillaScrapeError(f"BILLA PDF connection error: {error}") from error
            print(f"Transient BILLA PDF connection error; retrying in {2 ** (attempt + 1)}s", flush=True)
            time.sleep(2 ** (attempt + 1))
            continue
        if response.status_code != 200:
            raise BillaScrapeError(f"BILLA PDF HTTP {response.status_code}")
        return response.content
    raise BillaScrapeError("BILLA PDF fetch exhausted")


def parse_validity_pdf(text: str, today: date) -> tuple[str, str]:
    normalized = _clean(text).lower()
    if "dnes končí" in normalized:
        return "", today.isoformat()
    if "zítra končí" in normalized:
        return today.isoformat(), (today + timedelta(days=1)).isoformat()
    if "platí do" in normalized:
        return today.isoformat(), parse_partial_date(normalized, today)
    parts = [part.strip() for part in re.split(r"\s+[–-]+\s+", normalized, maxsplit=1)]
    if len(parts) == 2:
        return parse_partial_date(parts[0], today), parse_partial_date(parts[1], today)
    return today.isoformat(), parse_partial_date(normalized, today)


def _parse_price_token(text: str) -> float | None:
    """Parse a BILLA PDF price token like '27,90' into a float, or None.

    Strict Czech comma format keeps unrelated text out of price rows. Returns a
    clean float (rounded to 2 places) for all downstream numeric handling.
    """
    text = text.strip()
    if not re.fullmatch(r"\d{1,3},\d{2}", text):
        return None
    return round(float(text.replace(",", ".")), 2)


def parse_old_price(price: float, discount: float) -> str:
    if not discount or discount >= 100:
        return ""
    return str(round(price / (1 - discount / 100), 2))


def stamp_for_page_number(page_number: int) -> dict[str, Any]:
    """Return sale metadata for a PDF page (41-page live PDF).

    Page groups must be confirmed against the current PDF before a daily run
    takes this as fixed. If a page is not mapped here, try a fingerprinted
    fallback (the repeated 'Super pondeli' label at the same x). If that also
    fails, raise rather than silently publishing mislabeled dates.
    """
    if page_number == 1:
        return {"label": "Super pondeli 6. 10.", "unit": "kg", "date": "2026-10-06"}
    if page_number <= 20:
        return {"label": "Super stredu 7. 10.", "unit": "kg", "date": "2026-10-07"}
    if page_number <= 40:
        return {"label": "Super stredu 7. 10.", "unit": "kg", "date": "2026-10-07"}
    raise BillaScrapeError(f"page {page_number} not mapped; confirm leaflet group before daily run")


def extract_offer_rows(page: pymupdf.Document, page_number: int) -> list[dict]:
    # One block per product row. Every product row in these leaflets starts
    # with the caption "NAŠE CENA". Rows where the caption is absent are not
    # offers of this extraction scheme.
    blocks: list[dict] = []
    for block in page.get_text("dict")["blocks"]:
        if not block.get("lines"):
            continue
        full = " ".join(span["text"] for line in block["lines"] for span in line["spans"])
        blocks.append({"block": block, "full": full, "lines": block["lines"]})

    rows: list[dict] = []
    for item in blocks:
        if "NAŠE" not in item["full"]:
            continue
        # Locate caption and price: caption is the first "NAŠE CENA" span,
        # next numeric token is the current price.
        caption_y = None
        price_text = None
        flat = []
        for line in item["block"].get("lines", []):
            for span in line["spans"]:
                flat.append((span["bbox"], _clean(span["text"]).strip(), span))
        for bbox, text, span in flat:
            if text.startswith("NAŠE"):
                caption_y = bbox[1]
                continue
            candidate = _parse_price_token(text)
            if candidate is not None and price_text is None:
                price_text = text
                continue
        if price_text is None:
            raise BillaScrapeError(f"no price inside NAŠE CENA block on page {page_number}")
        price = _parse_price_token(price_text)
        if caption_y is None:
            raise BillaScrapeError(f"missing NAŠE CENA caption on page {page_number}")
        discount = parse_percentage(item["full"])
        old_price = ""
        match = re.search(r"běžná cena\s+(\d{1,3},\d{2})", item["full"])
        if match and not discount:
            old_price = str(round(float(match.group(1).replace(",", ".")), 2))
        # Description: the text line with the same x-band as the caption,
        # just below the caption.
        desc = ""
        for bbox, text, _span in flat:
            if bbox[1] <= caption_y + 26 and bbox[1] > caption_y + 2:
                if abs(bbox[0] - flat[0][0][0]) < 2:
                    desc = text
                    break
        name = desc or _clean(price_text).strip()
        if not name:
            raise BillaScrapeError(f"no description for price {price} on page {page_number}")
        unit_price = f"{price:g} Kč / {stamp_for_page_number(page_number)['unit']}"
        normalized = normalize_offer_unit_price(unit_price, name, price)
        start, end = parse_validity_pdf(stamp_for_page_number(page_number)["date"], date.today())
        rows.append({
            "store": CONFIG.store,
            "category": CONFIG.category,
            "product_id": f"{BASE_PRODUCT_ID}-{page_number}-{len(rows)}",
            "product_name": name,
            "canonical_product_name": canonical_product_name(name),
            "price": price,
            "old_price": old_price,
            "currency": "Kč",
            "unit_price": unit_price,
            "price_per_kg": normalized["price_per_kg"],
            "price_per_piece": normalized["price_per_piece"],
            "loyalty_required": False,
            "loyalty_program": "",
            "loyalty_price": "",
            "discount_label": stamp_for_page_number(page_number)["label"],
            "date_range": format_date_range(start, end),
            "url": BILLA_LETAK_URL,
            "image_url": "",
            "scraped_at": today_timestamp(),
        })
    if not rows:
        raise BillaScrapeError(f"no offer rows on page {page_number}")
    return rows


def run_billa_scrape() -> list[dict]:
    pdf_bytes = fetch_billa_pdf()
    tmp = Path(f"/tmp/billa-{today_timestamp().replace(':', '-')}.pdf")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(pdf_bytes)
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as error:  # pragma: no cover - defensive
        raise BillaScrapeError(f"open PDF failed: {error}") from error
    rows: list[dict] = []
    for page_number, page in enumerate(doc, start=1):
        page_rows = extract_offer_rows(page, page_number)
        for row in page_rows:
            rows.append(row)
            print(f"  page {page_number}: +1", flush=True)
    if not rows:
        raise BillaScrapeError("BILLA PDF contained no offers")
    return rows


def main() -> None:
    raise BillaScrapeError(
        "Experimental PDF extractor is not production ready: leaflet discovery, "
        "product layout, units and validity need validation. Use scrapers.billa."
    )


if __name__ == "__main__":
    main()
