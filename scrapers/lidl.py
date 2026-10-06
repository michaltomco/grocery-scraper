"""Public Lidl.cz produce offers, using embedded product-card JSON."""

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.common import (
    FIELDNAMES,
    HISTORY_CSV,
    LOCAL_TIMEZONE,
    ROOT,
    append_history,
    canonical_product_name,
    clean_text,
    format_date_range,
    normalize_unit_price,
    today_timestamp,
    write_csv,
)

URL = "https://www.lidl.cz/c/ovoce-a-zelenina/a10008734"
CSV_PATH = ROOT / "lidl.csv"


@dataclass(frozen=True)
class LidlStoreConfig:
    store: str = "Lidl"
    csv_path: Path = CSV_PATH


CONFIG = LidlStoreConfig()


def lidl_unit_price(text: str, price: float) -> str:
    """Convert Lidl's quantity-first references into the shared CSV notation."""
    text = clean_text(text)
    reference = re.search(
        r"(\d+(?:[,.]\d+)?)\s*(kg|g|ks|kus)\s*=\s*(\d+(?:[,.]\d+)?)\s*Kč",
        text,
    )
    if reference:
        quantity, unit, amount = reference.groups()
        return f"{amount} Kč / {quantity} {'ks' if unit == 'kus' else unit}"
    quantity = re.match(r"(?:(\d+(?:[,.]\d+)?)\s*)?(kg|g|ks|kus)\b", text)
    if quantity:
        amount, unit = quantity.groups()
        return f"{price:g} Kč / {amount or '1'} {'ks' if unit == 'kus' else unit}"
    return text


def extract_lidl_products(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    scraped_at = today_timestamp()
    offers = {}
    for element in soup.select("[data-grid-data]"):
        card = json.loads(element["data-grid-data"])
        if not isinstance(card, dict) or not card.get("productId"):
            continue
        pricing = card["price"]
        price = float(pricing["price"])
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Lidl returned an invalid product price")
        if pricing.get("currencyCode") != "CZK":
            raise ValueError("Unexpected Lidl currency")
        # This source covers ordinary public offers. Do not silently treat a
        # loyalty card or a variable/from price as an unconditional price.
        if "lidl plus" in json.dumps(card, ensure_ascii=False).lower() or pricing.get(
            "variantsHaveDifferentPrices"
        ) or pricing.get("discount", {}).get("showFrom"):
            raise ValueError("Unsupported conditional Lidl price")
        name = clean_text(card.get("fullTitle") or card["title"])
        start = datetime.fromtimestamp(card["storeStartDate"], LOCAL_TIMEZONE).date()
        end = datetime.fromtimestamp(card["storeEndDate"], LOCAL_TIMEZONE).date()
        if not name or end < start:
            raise ValueError("Invalid Lidl product name or validity dates")
        unit_price = lidl_unit_price(pricing.get("basePrice", {}).get("text", ""), price)
        normalized = normalize_unit_price(unit_price)
        discount = pricing.get("discount", {})
        old_price = pricing.get("oldPrice") or discount.get("deletedPrice") or ""
        percent = discount.get("percentageDiscount")
        row = dict.fromkeys(FIELDNAMES, "")
        row.update(
            store="Lidl",
            category="Ovoce a zelenina",
            product_id=str(card["productId"]),
            product_name=name,
            canonical_product_name=canonical_product_name(name),
            price=price,
            old_price=old_price,
            currency="Kč",
            unit_price=unit_price,
            **normalized,
            loyalty_required=False,
            discount_label=f"-{percent:g}%" if percent else discount.get("discountText", ""),
            date_range=format_date_range(start.isoformat(), end.isoformat()),
            url=urljoin(URL, card["canonicalUrl"]),
            image_url=card.get("image", ""),
            scraped_at=scraped_at,
        )
        key = (row["product_id"], row["date_range"], row["price"], row["unit_price"])
        offers[key] = row
    return list(offers.values())


def fetch_lidl_products() -> list[dict]:
    response = requests.get(
        URL,
        headers={"User-Agent": "grocery-scraper/0.1", "Accept-Language": "cs-CZ,cs;q=0.9"},
        timeout=30,
    )
    response.raise_for_status()
    return extract_lidl_products(response.text)


def main(csv_path: Path | None = None, history_path: Path | None = HISTORY_CSV) -> None:
    if csv_path is None:
        csv_path = CONFIG.csv_path
    products = fetch_lidl_products()
    if not products:
        raise ValueError("No Lidl product cards found; existing snapshot preserved")
    write_csv(csv_path, products)
    if history_path is not None:
        append_history(history_path, products)
    print(f"Lidl.cz: saved {len(products)} produce offers to {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=CSV_PATH)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args()
    main(args.output, None if args.no_history else HISTORY_CSV)
