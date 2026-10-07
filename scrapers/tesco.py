"""Current Tesco Online offers from the public catalogue GraphQL API."""

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests

from scrapers.common import (
    FIELDNAMES, HISTORY_CSV, LOCAL_TIMEZONE, ROOT, append_history,
    canonical_product_name, clean_text, format_date_range,
    normalize_offer_unit_price, today_timestamp, write_csv,
)

API = "https://xapi.tesco.com/q/"
FOOD_DEPARTMENTS = {
    "Ovoce a zelenina", "Mléčné, vejce a margaríny", "Pekárna",
    "Maso a lahůdky", "Mražené", "Trvanlivé", "Nápoje", "Speciální výživa",
}
TAXONOMY_QUERY = "query GetTaxonomy { taxonomy { id name } }"
PRODUCT_QUERY = """query GetCategoryProducts($facet: ID, $page: Int, $count: Int) {
  category(facet: $facet, page: $page, count: $count, offers: false) {
    info { total page count }
    products {
      id gtin title superDepartmentName isForSale productType
      price { actual unitPrice unitOfMeasure }
      defaultImageUrl
      promotions {
        promotionId: id startDate endDate offerText: description
        promotionAttributes: attributes qualities
        price { beforeDiscount afterDiscount } unitSellingInfo
      }
    }
  }
}"""


@dataclass(frozen=True)
class TescoStoreConfig:
    store: str = "Tesco"
    csv_path: Path = ROOT / "tesco.csv"


CONFIG = TescoStoreConfig()


def positive_price(value) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Tesco returned an invalid price")
    return number


def extract_tesco_offers(products: list[dict], now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(LOCAL_TIMEZONE)
    rows = {}
    scraped_at = today_timestamp()
    for product in products:
        if product["superDepartmentName"] not in FOOD_DEPARTMENTS or not product["isForSale"]:
            continue
        name = clean_text(product["title"])
        if not name or not product["id"]:
            raise ValueError("Tesco returned an invalid product identity")
        pricing = product["price"]
        loose = product["productType"] == "LooseProduce"
        regular = positive_price(pricing["unitPrice"] if loose else pricing["actual"])
        for promotion in product["promotions"]:
            qualities = promotion["qualities"]
            # Quantity-dependent offers are not unconditional single-item prices.
            if "multibuy" in qualities or "price_cut" not in qualities:
                continue
            start = datetime.fromisoformat(promotion["startDate"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(promotion["endDate"].replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                raise ValueError("Tesco returned invalid promotion dates")
            if end <= now:
                continue
            loyalty = "CLUBCARD_PRICING" in promotion["promotionAttributes"]
            label = clean_text(promotion["offerText"])
            if loyalty:
                # afterDiscount can contain the ordinary price for Clubcard offers.
                match = re.search(r"(?<!\d)(\d+(?:[,.]\d+)?)\s*Kč", label)
                if not match:
                    raise ValueError(f"No explicit Clubcard price for {name}: {label}")
                price = positive_price(match[1].replace(",", "."))
                old = regular
            else:
                price = positive_price(promotion["price"]["afterDiscount"])
                previous = promotion["price"].get("beforeDiscount")
                old = positive_price(previous) if previous is not None else ""
            unit = clean_text(promotion["unitSellingInfo"] or "").replace("/kus", "/ks")
            if not unit:
                raise ValueError(f"No promotion unit reference for {name}")
            row = dict.fromkeys(FIELDNAMES, "")
            row.update(
                store="Tesco", category=product["superDepartmentName"],
                product_id=f"tesco-online-{product['id']}", product_name=name,
                canonical_product_name=canonical_product_name(name), price=price,
                old_price=old, currency="Kč", unit_price=unit,
                **normalize_offer_unit_price(unit, name, price),
                loyalty_required=loyalty, loyalty_program="Clubcard" if loyalty else "",
                loyalty_price=price if loyalty else "", discount_label=label,
                date_range=format_date_range(
                    start.astimezone(LOCAL_TIMEZONE).date().isoformat(),
                    (end.astimezone(LOCAL_TIMEZONE) - timedelta(microseconds=1)).date().isoformat(),
                ),
                url=f"https://nakup.itesco.cz/shop/cs-CZ/products/{product['id']}",
                image_url=product.get("defaultImageUrl") or "", scraped_at=scraped_at,
            )
            rows[(row["product_id"], promotion["promotionId"], price)] = row
    return list(rows.values())


def query(session: requests.Session, operation: str, document: str, variables: dict) -> dict:
    response = session.post(API + operation, json={"query": document, "variables": variables}, timeout=45)
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors") or payload.get("status", 200) != 200 or not payload.get("data"):
        raise ValueError(f"Tesco GraphQL request failed: {operation}")
    return payload["data"]


def fetch_tesco_offers() -> list[dict]:
    # Public frontend client identifier, not a user credential.
    public_client = json.loads(Path(__file__).with_name("tesco_client.json").read_text())
    with requests.Session() as session:
        session.headers.update({
            "x-apikey": os.environ.get("TESCO_PUBLIC_API_KEY", public_client["api_key"]),
            "region": "CZ", "language": "cs-CZ", "Accept-Language": "cs-CZ",
            "Accept": "application/json", "User-Agent": "grocery-scraper/0.1",
        })
        taxonomy = query(session, "GetTaxonomy", TAXONOMY_QUERY, {})["taxonomy"]
        departments = {item["name"]: item["id"] for item in taxonomy if item["name"] in FOOD_DEPARTMENTS}
        if departments.keys() != FOOD_DEPARTMENTS:
            raise ValueError("Tesco food taxonomy changed; existing snapshot preserved")
        products = {}
        for name, facet in departments.items():
            seen = set()
            expected_total = None
            for page in range(1, 101):
                category = query(session, "GetCategoryProducts", PRODUCT_QUERY,
                                 {"facet": facet, "page": page, "count": 300})["category"]
                info, items = category["info"], category["products"]
                total = info["total"]
                if expected_total is None:
                    expected_total = total
                if total != expected_total or info["page"] != page or info["count"] != len(items):
                    raise ValueError(f"Inconsistent Tesco pagination: {name}")
                ids = {item["id"] for item in items}
                if len(ids) != len(items) or seen & ids:
                    raise ValueError(f"Repeated Tesco page: {name}")
                seen.update(ids)
                products.update({item["id"]: item for item in items})
                if len(seen) == total:
                    break
                if not items or len(seen) > total:
                    raise ValueError(f"Truncated Tesco catalogue: {name}")
            else:
                raise ValueError(f"Too many Tesco pages: {name}")
            print(f"Tesco Online: {name}: {len(seen)} products", flush=True)
    return extract_tesco_offers(list(products.values()))


def main(csv_path: Path | None = None, history_path: Path | None = HISTORY_CSV) -> None:
    rows = fetch_tesco_offers()
    if not rows:
        raise ValueError("No Tesco promotions found; existing snapshot preserved")
    write_csv(csv_path or CONFIG.csv_path, rows)
    if history_path is not None:
        append_history(history_path, rows)
    print(f"Tesco Online: saved {len(rows)} offers")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=CONFIG.csv_path)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args()
    main(args.output, None if args.no_history else HISTORY_CSV)
