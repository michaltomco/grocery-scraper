import csv
from collections import defaultdict
from pathlib import Path

try:
    from scrapers.common import read_csv
except ModuleNotFoundError:
    from common import read_csv


ROOT = Path(__file__).resolve().parent
HISTORY_CSV = ROOT / "history.csv"
REPORT_CSV = ROOT / "price_report.csv"
REPORT_FIELDS = [
    "canonical_product_name",
    "best_store",
    "best_price",
    "best_price_per_kg",
    "best_date_range",
    "dropped_count",
    "increased_count",
    "loyalty_deals_count",
]


def number(value: str) -> float | None:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def is_true(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def money(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def describe_change(current: float, previous: float | None) -> tuple[str, float | None, float | None]:
    if previous is None:
        return "unchanged", None, None
    delta = current - previous
    percent = delta / previous * 100 if previous else None
    if abs(delta) < 0.005:
        return "unchanged", 0.0, 0.0 if previous else None
    return ("dropped" if delta < 0 else "increased"), delta, percent


def write_report(rows: list[dict]) -> None:
    with REPORT_CSV.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    if not HISTORY_CSV.exists():
        write_report([])
        print("No history.csv found; no price report was generated.")
        return

    history = read_csv(HISTORY_CSV)
    if not history:
        write_report([])
        print("history.csv is empty; no price report was generated.")
        return

    by_store = defaultdict(list)
    for row in history:
        by_store[row.get("store", "")].append(row)

    current_rows = []
    previous_by_key = {}
    for store, rows in by_store.items():
        timestamps = sorted({row.get("scraped_at", "") for row in rows}, reverse=True)
        current_timestamp = timestamps[0] if timestamps else ""
        previous_timestamp = timestamps[1] if len(timestamps) > 1 else None
        current_rows.extend(row for row in rows if row.get("scraped_at", "") == current_timestamp)
        if previous_timestamp:
            for row in rows:
                if row.get("scraped_at", "") == previous_timestamp:
                    key = (store, row.get("product_id", ""))
                    price = number(row.get("price", ""))
                    if price is not None and (key not in previous_by_key or price < previous_by_key[key]):
                        previous_by_key[key] = price

    products = defaultdict(list)
    for row in current_rows:
        products[row.get("canonical_product_name", "")].append(row)

    report_rows = []
    changes = []
    loyalty_deals = []
    for product, rows in sorted(products.items()):
        kg_rows = [(number(row.get("price_per_kg", "")), row) for row in rows]
        kg_rows = [(price, row) for price, row in kg_rows if price is not None]
        candidates = kg_rows or [(number(row.get("price", "")), row) for row in rows]
        candidates = [(price, row) for price, row in candidates if price is not None]
        best_price, best = min(candidates, key=lambda item: item[0]) if candidates else (None, {})

        dropped = increased = loyalty = 0
        for row in rows:
            current_price = number(row.get("price", ""))
            previous_price = previous_by_key.get((row.get("store", ""), row.get("product_id", "")))
            if current_price is not None:
                label, delta, percent = describe_change(current_price, previous_price)
                if label == "dropped":
                    dropped += 1
                elif label == "increased":
                    increased += 1
                if label != "unchanged" and previous_price is not None:
                    changes.append((product, row, label, delta, percent))
            if is_true(row.get("loyalty_required", "")):
                loyalty += 1
                loyalty_deals.append((product, row))

        report_rows.append({
            "canonical_product_name": product,
            "best_store": best.get("store", ""),
            "best_price": money(number(best.get("price", ""))),
            "best_price_per_kg": money(number(best.get("price_per_kg", ""))),
            "best_date_range": best.get("date_range", ""),
            "dropped_count": dropped,
            "increased_count": increased,
            "loyalty_deals_count": loyalty,
        })

    write_report(report_rows)
    print("BEST PRICES")
    for product in sorted(products.keys()):
        entries = []
        for row in products[product]:
            store = row.get("store", "")
            kg = number(row.get("price_per_kg", ""))
            if kg is not None:
                disp = f"{kg:.2f} / kg"
                comp = kg
            else:
                comp = number(row.get("price", ""))
                disp = money(comp)
            if comp is None:
                comp = float("inf")
            entries.append((store, disp, comp))
        if not entries:
            continue
        best_comp = min(e[2] for e in entries)
        entries.sort(key=lambda e: e[2])
        parts = [
            f"{store} {disp}{' (best)' if comp == best_comp else ''}"
            for store, disp, comp in entries
        ]
        print(f"  {product}: " + " | ".join(parts))
    print("\nNOTABLE PRICE DROPS")
    drops = [item for item in changes if item[2] == "dropped"]
    if drops:
        for product, row, label, delta, percent in sorted(drops, key=lambda item: item[3]):
            print(f"  {product} — {row['store']}: {money(number(row.get('price', '')))} ({delta:+.2f}, {percent:+.1f}%)")
    else:
        print("  None")
    print("\nLOYALTY DEALS")
    if loyalty_deals:
        for product, row in loyalty_deals:
            print(f"  {product} — {row['store']}: {row.get('loyalty_price') or row.get('price', '—')} {row.get('currency', '')} ({row.get('loyalty_program') or 'loyalty'})")
    else:
        print("  None")
    print(f"\nWrote {len(report_rows)} product rows to {REPORT_CSV.name}.")


if __name__ == "__main__":
    main()
