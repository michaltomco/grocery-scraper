# grocery-scraper

Scrapes Albert, Lidl, and Tesco fruit and vegetable discounts from Kupi.cz.

## Running

Use the repository virtualenv. The top-level runner works from any current
directory:

```bash
/home/mito/Projects/grocery-scraper/.venv/bin/python \
  /home/mito/Projects/grocery-scraper/run.py
```

Individual scrapers remain available:

```bash
/home/mito/Projects/grocery-scraper/.venv/bin/python \
  /home/mito/Projects/grocery-scraper/scrapers/albert.py
```

Each scraper refreshes its store snapshot (`albert.csv`, `lidl.csv`, or
`tesco.csv`) and appends unseen offers to `history.csv`. The runner then
refreshes `all_discounts.csv` from the three snapshots. All files are written
at the repository root, regardless of the current working directory.

## Tests

The regression suite uses Python's built-in `unittest` module, so no separate
test runner is required:

```bash
uv run python -m unittest discover -s tests -v
```

The suite includes real Chromium interaction tests. Install the browser once
locally before the first run:

```bash
uv run playwright install chromium
```

GitHub Actions installs Chromium and runs the same test command on every push
and pull request.

## Kupi historical price trends

`history.csv` contains exact store offers observed by this scraper. For earlier
history, Kupi product pages expose a separate daily chart of aggregate prices.
The backfill stores those graph points in `kupi_price_history.csv` so they are
not confused with exact store-level offers:

```bash
uv run python kupi_history.py
```

The graph CSV contains Kupi's lowest promotional price, average promotional
price, and regular price series, with `source=kupi_graph`. It can cover roughly
three months depending on the product, but it is not a complete historical list
of every store's individual offer.

## CSV schema

All output CSVs use the same columns, in this order:

`store`, `city`, `store_location`, `category`, `product_id`, `product_name`,
`canonical_product_name`, `price`, `old_price`, `currency`, `unit_price`,
`price_per_kg`, `price_per_piece`, `loyalty_required`, `loyalty_program`,
`loyalty_price`, `loyalty_old_price`, `discount_label`, `availability`,
`start_date`, `end_date`, `date_range`, `url`, `scraped_at`.

History deduplication uses `store + product_id + date_range + price +
unit_price`; repeated runs therefore preserve one record for each identical
offer while new dates or prices are retained.

## Nutrition data

The site enriches each canonical produce item with nutrition values per 100 g.
Missing items are looked up from Open Food Facts during `build_site.py`, then
persisted in `nutrition_cache.json`; cached items are not requested again.
The cache records successful, unavailable, and failed lookups so a site rebuild
does not repeatedly contact the remote service.
