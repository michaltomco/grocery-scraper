import csv
from collections import defaultdict
from datetime import date, datetime, timedelta
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
    "best_unit",
    "best_date_range",
    "dropped_count",
    "increased_count",
]


def number(value: str) -> float | None:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def is_true(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def normalized_price(row: dict) -> tuple[float | None, str]:
    """Return (value, unit) normalized to /kg or /ks, else (None, '')."""
    kg = number(row.get("price_per_kg", ""))
    if kg is not None:
        return kg, "kg"
    pc = number(row.get("price_per_piece", ""))
    if pc is not None:
        return pc, "ks"
    return None, ""


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

    today_iso = date.today().isoformat()
    today_date = date.fromisoformat(today_iso)
    STALE_HORIZON = today_date + timedelta(days=14)

    all_scrape_dates = sorted(
        {row.get("scraped_at", "")[:10] for row in history if row.get("scraped_at")},
        reverse=True,
    )
    has_history = len(all_scrape_dates) > 1
    previous_day = (
        max(
            d
            for d in all_scrape_dates
            if date.fromisoformat(d) < today_date
        )
        if any(date.fromisoformat(d) < today_date for d in all_scrape_dates)
        else None
    )

    # ACTIVE-WINDOW model (mirrors build_site.py): show every discount still
    # valid today — not only rows from the single global latest scrape day.
    # Stores finish their daily cron on different calendar days and Kupi flyers
    # are forward-dated, so a product scraped a few days ago with a range
    # covering today is a current offer and must be reported. For previous-day
    # price comparison we use the most-recent scrape day that is strictly before
    # today (so a store whose latest scrape was yesterday still gets a baseline).
    current_rows = []
    previous_by_key = {}
    for row in history:
        store = row.get("store", "")
        s, en = (row.get("date_range", "") or "").split(" - ", 1) if " - " in (row.get("date_range", "") or "") else (row.get("date_range", ""), row.get("date_range", ""))
        end = date.fromisoformat(en) if en else None
        start = date.fromisoformat(s) if s else None
        if end is None:
            active = True
        else:
            active = end >= today_date and (start is None or start <= STALE_HORIZON)
        if active:
            current_rows.append(row)
        if previous_day and row.get("scraped_at", "").startswith(previous_day):
            key = (store, row.get("product_id", ""))
            price = number(row.get("price", ""))
            if price is not None and (key not in previous_by_key or price < previous_by_key[key]):
                previous_by_key[key] = price

    products = defaultdict(list)
    for row in current_rows:
        products[row.get("canonical_product_name", "")].append(row)

    report_rows = []
    changes = []
    for product, rows in sorted(products.items()):
        candidates = []
        for row in rows:
            val, unit = normalized_price(row)
            if val is not None:
                candidates.append((val, unit, row))
        best = min(candidates, key=lambda c: c[0]) if candidates else (None, "", {})

        dropped = increased = 0
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

        report_rows.append({
            "canonical_product_name": product,
            "best_store": best[2].get("store", ""),
            "best_price": f"{best[0]:.2f}" if best[0] is not None else "",
            "best_unit": f"/{best[1]}" if best[1] else "",
            "best_date_range": best[2].get("date_range", ""),
            "dropped_count": dropped,
            "increased_count": increased,
        })

    write_report(report_rows)
    print("BEST PRICES")
    for product in sorted(products.keys()):
        # One entry per store, taking the cheapest still-active offer (a store
        # may list the same item on multiple active flyers).
        best_per_store: dict[str, tuple[float, str]] = {}
        for row in products[product]:
            val, unit = normalized_price(row)
            if val is None:
                continue
            store = row.get("store", "")
            if is_true(row.get("loyalty_required", "")):
                lp = number(row.get("loyalty_price", ""))
                if lp is not None:
                    val = lp
            prev = best_per_store.get(store)
            if prev is None or val < prev[0]:
                best_per_store[store] = (val, unit)
        if not best_per_store:
            continue
        entries = [(store, v, u) for store, (v, u) in best_per_store.items()]
        best_val = min(e[1] for e in entries)
        parts = [
            f"{store} {val:.2f}/{unit}{' (best)' if val == best_val else ''}"
            for store, val, unit in sorted(entries, key=lambda e: e[1])
        ]
        print(f"  {product}: " + " | ".join(parts))
    print("\nNOTABLE PRICE DROPS")
    drops = [item for item in changes if item[2] == "dropped"]
    if drops:
        for product, row, label, delta, percent in sorted(drops, key=lambda item: item[3]):
            val, unit = normalized_price(row)
            disp = f"{val:.2f}/{unit}" if val is not None else f"{number(row.get('price', '')):.2f}"
            print(f"  {product} — {row['store']}: {disp} ({delta:+.2f}, {percent:+.1f}%)")
    else:
        print("  None")
    print(f"\nWrote {len(report_rows)} product rows to {REPORT_CSV.name}.")


if __name__ == "__main__":
    main()
