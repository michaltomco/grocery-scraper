"""Backfill Kupi's product-level historical price graph.

Kupi's /graph endpoint returns daily chart series for a product ID. These are
separate from history.csv: history.csv contains exact store offers observed by
this scraper, while this module stores Kupi's aggregate graph series (lowest
promotional price, average promotional price, and regular price).
"""

import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests

from scrapers.common import (
    HISTORY_CSV,
    KUPI_BASE_URL,
    KUPI_HEADERS,
    LOCAL_TIMEZONE,
    canonical_product_name,
    read_csv,
)


ROOT = Path(__file__).resolve().parent
KUPI_PRICE_HISTORY_CSV = ROOT / "kupi_price_history.csv"
KUPI_PRICE_HISTORY_FIELDS = [
    "product_id",
    "canonical_product_name",
    "product_name",
    "observed_date",
    "series",
    "price",
    "currency",
    "unit",
    "store_logo_url",
    "source",
]
GRAPH_SERIES = {
    "low": "lowest_discount",
    "avg": "average_discount",
    "bef": "regular_price",
}
POINT_RE = re.compile(r"\[\s*(\d+)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(.*?)\s*\]")


def parse_kupi_graph_html(
    html: str,
    product_id: str,
    canonical_product_name: str,
    product_name: str,
) -> list[dict]:
    """Parse Kupi's embedded graph_data JSON into daily trend rows."""
    marker = "var graph_data = "
    start = html.find(marker)
    if start < 0:
        return []
    try:
        graph_data = json.JSONDecoder().raw_decode(html[start + len(marker):])[0]
    except (json.JSONDecodeError, ValueError):
        return []

    unit = str(graph_data.get("unit", ""))
    rows = []
    for graph_key, series in GRAPH_SERIES.items():
        points = str(graph_data.get(graph_key, ""))
        for timestamp_ms, price, logo in POINT_RE.findall(points):
            observed_date = datetime.fromtimestamp(
                int(timestamp_ms) / 1000, LOCAL_TIMEZONE
            ).date().isoformat()
            rows.append(
                {
                    "product_id": str(product_id),
                    "canonical_product_name": canonical_product_name,
                    "product_name": product_name,
                    "observed_date": observed_date,
                    "series": series,
                    "price": float(price),
                    "currency": "Kč",
                    "unit": unit,
                    "store_logo_url": logo.strip(),
                    "source": "kupi_graph",
                }
            )
    return rows


def fetch_kupi_graph(product_id: str) -> str:
    response = requests.post(
        f"{KUPI_BASE_URL}/graph",
        headers=KUPI_HEADERS,
        data={"graph[product]": product_id},
        timeout=20,
    )
    response.raise_for_status()
    return response.text


def history_products() -> list[dict]:
    """Return one latest representative for every Kupi product ID we observed."""
    latest: dict[str, dict] = {}
    for row in read_csv(HISTORY_CSV):
        product_id = row.get("product_id", "")
        if not product_id:
            continue
        previous = latest.get(product_id)
        if previous is None or row.get("scraped_at", "") > previous.get("scraped_at", ""):
            latest[product_id] = row
    return list(latest.values())


def row_key(row: dict) -> tuple[str, ...]:
    return tuple(str(row.get(field, "")) for field in KUPI_PRICE_HISTORY_FIELDS[:-1])


def read_kupi_price_history(path: Path = KUPI_PRICE_HISTORY_CSV) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_kupi_price_history(rows: list[dict], path: Path = KUPI_PRICE_HISTORY_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [{field: str(row.get(field, "")) for field in KUPI_PRICE_HISTORY_FIELDS} for row in rows]
    normalized.sort(key=lambda row: (row["canonical_product_name"], row["observed_date"], row["series"]))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=KUPI_PRICE_HISTORY_FIELDS)
        writer.writeheader()
        writer.writerows(normalized)


def backfill(delay_seconds: float = 0.15) -> list[dict]:
    """Fetch graph history for every observed Kupi product ID and merge it.

    A short delay avoids hammering Kupi while backfilling the local catalogue.
    Individual request failures are reported and skipped; existing rows remain.
    """
    existing = read_kupi_price_history()
    merged = {row_key(row): row for row in existing}
    products = history_products()
    for index, product in enumerate(products, 1):
        product_id = product["product_id"]
        try:
            html = fetch_kupi_graph(product_id)
            rows = parse_kupi_graph_html(
                html,
                product_id,
                canonical_product_name(product.get("product_name", "")),
                product.get("product_name", ""),
            )
        except requests.RequestException as error:
            print(f"Could not fetch Kupi graph for {product_id}: {error}")
            continue
        for row in rows:
            merged[row_key(row)] = row
        print(f"{index}/{len(products)} {product_id}: {len(rows)} graph points")
        if delay_seconds:
            time.sleep(delay_seconds)
    rows = list(merged.values())
    write_kupi_price_history(rows)
    return rows


def main() -> None:
    rows = backfill()
    print(f"Wrote {len(rows)} Kupi historical trend rows to {KUPI_PRICE_HISTORY_CSV.name}.")


if __name__ == "__main__":
    main()
