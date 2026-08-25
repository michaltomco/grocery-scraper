"""Resumable, checkpointing backfill of Kupi graph history for ALL categories.

This wraps the same fetch/parse primitives used for the fruit & vegetable
backfill (kupi_history.fetch_kupi_graph / parse_kupi_graph_html) but is safe to
run repeatedly: it skips product IDs already present in kupi_price_history.csv
and rewrites the CSV every `checkpoint_every` products, so an interrupted run
resumes without re-hammering Kupi for work already done.
"""

import time
from pathlib import Path

from kupi_history import (
    KUPI_PRICE_HISTORY_CSV,
    fetch_kupi_graph,
    history_products,
    parse_kupi_graph_html,
    read_kupi_price_history,
    row_key,
    write_kupi_price_history,
)

CHECKPOINT_EVERY = 100
DELAY_SECONDS = 0.15


def main() -> None:
    existing = read_kupi_price_history()
    merged = {row_key(row): row for row in existing}
    done_ids = {row["product_id"] for row in existing}

    products = history_products()
    total = len(products)
    processed = 0
    skipped = 0
    fetched = 0

    print(f"Starting backfill: {total} distinct product IDs, "
          f"{len(done_ids)} already present.")
    for index, product in enumerate(products, 1):
        product_id = product["product_id"]
        if product_id in done_ids:
            skipped += 1
            continue
        try:
            html = fetch_kupi_graph(product_id)
            rows = parse_kupi_graph_html(
                html,
                product_id,
                product.get("canonical_product_name")
                or "",  # recompute if missing
                product.get("product_name", ""),
            )
        except Exception as error:  # noqa: BLE001 - one bad product must not abort
            print(f"  ! {product_id}: failed: {error}")
            if DELAY_SECONDS:
                time.sleep(DELAY_SECONDS)
            continue
        for row in rows:
            merged[row_key(row)] = row
        fetched += 1
        done_ids.add(product_id)
        print(f"  {index}/{total} {product_id}: +{len(rows)} points")
        if DELAY_SECONDS:
            time.sleep(DELAY_SECONDS)
        if fetched % CHECKPOINT_EVERY == 0:
            write_kupi_price_history(list(merged.values()))
            print(f"  -- checkpoint: {len(merged)} rows written --")

    rows = list(merged.values())
    write_kupi_price_history(rows)
    print(f"Done. Total rows: {len(rows)} "
          f"(fetched {fetched} new IDs, skipped {skipped} already-done).")


if __name__ == "__main__":
    main()
