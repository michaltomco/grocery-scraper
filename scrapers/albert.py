"""Public Můj Albert app leaflet offers, with explicit physical-store selection."""

import argparse
import math
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import requests

from scrapers.common import (
    FIELDNAMES,
    HISTORY_CSV,
    ROOT,
    append_history,
    canonical_product_name,
    clean_text,
    format_date_range,
    normalize_unit_price,
    today_timestamp,
    write_csv,
)

API_BASE = "https://albertloyaltycz-albert-service-app-prod.delhaize.eu/public"
STORES_URL = f"{API_BASE}/store/v2"
LEAFLET_URL = f"{API_BASE}/content/leaflet/v3"
FOOD_CATEGORIES = {
    "ALBERT_MARKET", "ALBERT_BAKERY", "SAUSAGES", "MEAT_AND_FISH",
    "DAIRY", "DURABLES", "DRINKS", "FROZEN",
}
NON_FOOD_CATEGORIES = {"DRUGSTORE", "OTHERS"}
HEADERS = {"User-Agent": "grocery-scraper/0.1", "Accept": "application/json"}


@dataclass(frozen=True)
class AlbertStoreConfig:
    store: str = "Albert"
    csv_path: Path = ROOT / "albert.csv"
    # Preferred supermarket: Praha 5, Zlatý Anděl, Plzeňská 344/1.
    store_ids: tuple[str, ...] = ("583",)


CONFIG = AlbertStoreConfig()


def leaflet_url(store_id: str) -> str:
    return f"{LEAFLET_URL}?{urlencode({'storeId': store_id})}"


def fetch_json(url: str) -> dict:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Albert returned an unexpected JSON response")
    return payload


def fetch_albert_stores() -> list[dict]:
    payload = fetch_json(STORES_URL)
    if not isinstance(payload.get("stores"), list):
        raise ValueError("Albert store catalogue is missing")
    return [store for store in payload["stores"] if not store.get("deleted")]


def positive_price(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid Albert price: {value!r}")
    price = float(value)
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"Invalid Albert price: {value!r}")
    return price


def offer_quantity(text: str) -> str:
    """Normalize the API's priced volume, including Czech piece inflections."""
    match = re.fullmatch(
        r"(\d+(?:[.,]\d+)?)\s*(kg|g|ml|l|ks|kus|kusy|kusů)",
        clean_text(text),
    )
    if not match:
        raise ValueError(f"Unsupported Albert priced volume: {text!r}")
    amount, unit = match.groups()
    quantity = float(amount.replace(",", "."))
    if quantity <= 0:
        raise ValueError("Albert returned a zero priced volume")
    return f"{quantity:g} {'ks' if unit.startswith('kus') else unit}"


def product_name(name: str, quantity: str) -> str:
    name = clean_text(name)
    if not name:
        raise ValueError("Albert returned an empty product name")
    trailing = re.search(r"\d+(?:[.,]\d+)?\s*(?:kg|g|ml|l|ks|kus|kusy|kusů)$", name)
    if trailing and offer_quantity(trailing.group()) == quantity:
        return name
    return f"{name} {quantity}"


def extract_albert_products(payload: dict, store_id: str) -> list[dict]:
    """Convert current/next public leaflets, retaining ordinary and member offers."""
    if "current" not in payload:
        raise ValueError("Albert response has no current leaflet field")
    sections = []
    for week in ("current", "next"):
        section = payload.get(week)
        if section is None:
            continue
        if not isinstance(section, dict) or not isinstance(section.get("items"), list):
            raise ValueError("Albert returned an invalid leaflet section")
        sections.append(section)
    scraped_at = today_timestamp()
    rows = {}
    images = {
        item["goldId"]: item.get("imageUrl", "")
        for section in sections
        for item in section["items"]
    }
    for section in sections:
        categories = {category["key"]: category["name"] for category in section["leafletCategories"]}
        for item in section["items"]:
            category_key = item["leafletCategory"]
            if category_key in NON_FOOD_CATEGORIES:
                continue
            if category_key not in FOOD_CATEGORIES:
                raise ValueError(f"Unknown Albert food category: {category_key}")
            start = date.fromisoformat(item["validFrom"]).isoformat()
            end = date.fromisoformat(item["validTo"]).isoformat()
            if end < start:
                raise ValueError("Albert returned reversed validity dates")
            if item["itemType"] not in ("PIECE", "WEIGHT"):
                raise ValueError("Unsupported Albert item type")
            pricing = item["price"]
            member_price = pricing["customerDiscount"]
            if bool(item["promoForMember"]) != (member_price is not None):
                raise ValueError("Albert membership flag disagrees with its price")
            # Related variants have their own price and volume. In particular,
            # never copy the parent's member price or package size to a variant.
            products = [(item["goldId"], item["longName"], item["pricedVolume"], pricing)]
            for variant in item["affiliateProducts"]:
                products.append((
                    variant["goldId"], variant["name"], variant["pricedVolume"],
                    {"original": variant["regularPrice"], "discount": variant["promoPrice"],
                     "discountPercentage": variant["discountPercentage"],
                     "customerDiscount": None, "customerDiscountPercentage": None},
                ))
            for gold_id, name, volume, product_price in products:
                if not gold_id:
                    raise ValueError("Albert returned an empty product ID")
                quantity = offer_quantity(volume)
                name = product_name(name, quantity)
                ordinary_price = positive_price(product_price["discount"])
                original = product_price["original"]
                old_price = positive_price(original) if original is not None else ""
                prices = [(ordinary_price, False, product_price["discountPercentage"])]
                if product_price["customerDiscount"] is not None:
                    prices.append((positive_price(product_price["customerDiscount"]), True,
                                   product_price["customerDiscountPercentage"]))
                for price, loyalty, percentage in prices:
                    unit_price = f"{price:g} Kč / {quantity}"
                    row = dict.fromkeys(FIELDNAMES, "")
                    row.update(
                        store=CONFIG.store,
                        category=categories[category_key],
                        # Store identity must survive history deduplication,
                        # whose key does not include the source URL.
                        product_id=f"albert-{store_id}-{gold_id}",
                        product_name=name,
                        canonical_product_name=canonical_product_name(name),
                        price=price,
                        old_price=old_price,
                        currency="Kč",
                        unit_price=unit_price,
                        **normalize_unit_price(unit_price),
                        loyalty_required=loyalty,
                        loyalty_program="Můj Albert" if loyalty else "",
                        loyalty_price=price if loyalty else "",
                        discount_label=" ".join(filter(None, (
                            f"-{percentage:g}%" if percentage is not None else "",
                            "Můj Albert" if loyalty else "",
                        ))),
                        date_range=format_date_range(start, end),
                        url=leaflet_url(store_id),
                        image_url=images.get(gold_id, ""),
                        scraped_at=scraped_at,
                    )
                    key = tuple(str(row[field]) for field in (
                        "product_id", "category", "date_range", "price", "unit_price", "loyalty_required"
                    ))
                    # Prefer the full primary product name when the same SKU
                    # also appears under another offer's related variants.
                    if key not in rows or gold_id == item["goldId"]:
                        rows[key] = row
    return list(rows.values())


def selected_store_ids(store_ids: tuple[str, ...] | None = None) -> tuple[str, ...]:
    if store_ids is None:
        configured = os.environ.get("ALBERT_STORE_IDS")
        store_ids = tuple(configured.split(",")) if configured is not None else CONFIG.store_ids
    ids = tuple(dict.fromkeys(value.strip() for value in store_ids))
    if not ids or any(not value.isascii() or not value.isdigit() for value in ids):
        raise ValueError("Albert store IDs must be a nonempty list of numeric IDs")
    return ids


def fetch_albert_products(store_ids: tuple[str, ...] | None = None) -> list[dict]:
    ids = selected_store_ids(store_ids)
    stores = {store["storeId"]: store for store in fetch_albert_stores()}
    unknown = set(ids) - stores.keys()
    if unknown:
        raise ValueError(f"Unknown or deleted Albert stores: {', '.join(sorted(unknown))}")
    rows = []
    for store_id in ids:
        offers = extract_albert_products(fetch_json(leaflet_url(store_id)), store_id)
        if not offers:
            raise ValueError(f"No Albert food offers for store {store_id}; snapshot preserved")
        print(f"Albert {store_id} ({stores[store_id]['name']}): {len(offers)} offers")
        rows.extend(offers)
    return rows


def main(
    csv_path: Path | None = None,
    history_path: Path | None = HISTORY_CSV,
    store_ids: tuple[str, ...] | None = None,
) -> None:
    products = fetch_albert_products(store_ids)
    if not products:
        raise ValueError("No Albert food offers returned; existing snapshot preserved")
    csv_path = CONFIG.csv_path if csv_path is None else csv_path
    write_csv(csv_path, products)
    if history_path is not None:
        append_history(history_path, products)
    print(f"Můj Albert: saved {len(products)} offers to {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-id", action="append", help="Albert store ID; repeat to collect multiple stores")
    parser.add_argument("--list-stores", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args()
    if args.list_stores:
        for store in fetch_albert_stores():
            print(f"{store['storeId']}\t{store['type']}\t{store['name']}\t{store['street']}")
    else:
        main(args.output, None if args.no_history else HISTORY_CSV,
             tuple(args.store_id) if args.store_id is not None else None)
