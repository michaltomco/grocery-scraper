# grocery-scraper

Albert offers come from the public Můj Albert app API, Lidl produce offers
come directly from Lidl.cz, and Tesco offers come from its public online catalogue.
Billa's daily price scraper still uses Kupi.cz; the direct PDF prototype is not
production-ready (see "Billa source status" below). Product-image builds do not
fetch or reuse Kupi images as a fallback for direct retailer images.

## Direct Tesco Online scraper

`uv run --no-dev python -m scrapers.tesco --output /tmp/tesco-preview.csv --no-history`

The daily refresh uses Tesco's public catalogue API, without a user login. It
walks all pages of eight food departments and extracts explicit single-item
promotions from the full catalogue. The API's `offers=true` filter omits some
current discounts, so it is deliberately unused. Nonfood and conditional
multibuy/meal deals are excluded. Clubcard prices come from explicit promotion
text and their own unit reference, since `afterDiscount` can contain the ordinary
price. Loose products use kilogram prices rather than estimated piece totals.

These are online reference-store prices, not prices for a selected physical
branch. The website labels this distinction and excludes legacy Kupi Tesco
rows from its current view once direct data exists; history remains on disk.
Other retailers' scopes are unchanged. Clubcard `old_price` is the current
ordinary price; nonmember promotions use explicit `beforeDiscount` when supplied.

`scrapers/tesco_client.json` contains the client identifier openly published in
Tesco's website configuration, not an account token. If Tesco rotates it,
`TESCO_PUBLIC_API_KEY` overrides the value. GraphQL errors, changed taxonomy,
repeated/truncated pages and missing price references abort before CSV writes.

## Billa source status

The daily refresh uses Kupi for Billa. The PDF prototype is retained in
`scrapers/billa_pdf.py` and cannot publish snapshots: its fixed leaflet URL is
expired, and its current layout parser does not reliably extract product names,
units, membership requirements or validity. PyMuPDF is declared and locked for
future work on that prototype.

## Albert app scraper

Run only Albert from the repository root:

```bash
uv run --no-dev python -m scrapers.albert
```

The scraper uses the app's public, unauthenticated JSON endpoints. No phone,
browser, account or access token is needed. It collects the current and next
leaflets, including related product variants, for the selected physical stores.
The preferred store is **583: Praha 5, Zlatý Anděl (supermarket),
Plzeňská 344/1**. By default, only this store is collected.

Find and select other stores, or create a preview without updating history:

```bash
uv run --no-dev python -m scrapers.albert --list-stores
uv run --no-dev python -m scrapers.albert --store-id 583 --output /tmp/albert-preview.csv --no-history
```

Repeat `--store-id` to select multiple stores. `ALBERT_STORE_IDS=583` also
sets the stores for both this command and `run.py`; explicit CLI IDs take
precedence. A normal run writes `albert.csv` and appends to `history.csv` only
after every selected store succeeds. Empty responses, invalid store IDs or
invalid offers raise an error before replacing the existing snapshot.

Food categories retain the app's Czech labels; drogerie and ostatní are excluded.
Ordinary and Můj Albert member prices become separate rows with their own unit
prices. Personalized coupons and loyalty-point rewards are not cash discounts
and are not collected. Related variants use their own prices and quantities,
with their parent offer's validity dates; member prices are not inferred for
variants. Missing product images stay empty unless the API supplies that
product's image elsewhere in the same response.

The shared CSV schema is unchanged. Product IDs include the selected store ID
(`albert-829-20440701`), so history keeps branch-specific offers distinct. The
`url` column links to the store's source API response, not a product detail page.
`old_price` uses the API's `original` regular price when supplied; it is not
estimated from a discount percentage. The separate `omnibusPrice` reference
is not represented by this CSV schema. Kupi graph backfills use only Kupi-source
rows, since retailer product IDs are not Kupi IDs.

The endpoints were found in Můj Albert Android 7.3.1 and verified live on
2026-10-06: `/public/store/v2` and `/public/content/leaflet/v3?storeId=829` on
`albertloyaltycz-albert-service-app-prod.delhaize.eu`. The archive's signing
certificate fingerprint matches Albert's public Android app association.
The APK is not part of this repository and is not required at runtime. This is
an undocumented app API, so changes to the endpoint or JSON format may require
maintenance.

## Direct Lidl scraper

Run only Lidl from the repository root:

```bash
uv run --no-dev python -m scrapers.lidl
```

This refreshes `lidl.csv` and appends to `history.csv`; `run.py` also uses this
direct scraper. To try it without changing the regular snapshot or history:

```bash
uv run --no-dev python -m scrapers.lidl --output /tmp/lidl-preview.csv --no-history
```

The first direct version covers the public fruit-and-vegetable category only,
replacing Lidl's previous all-food-category Kupi collection. It reads embedded
product-card JSON in one HTTP request, without a browser or login. It keeps the
shared CSV schema, including product URLs, images, validity dates and normalized
unit prices. It does not collect Lidl Plus coupons. Unsupported conditional
prices, invalid cards and empty pages fail before replacing the snapshot.
`old_price` is the displayed crossed-out reference price, which may represent
the lowest price in the preceding 30 days rather than a regular price.

## Refresh reliability

Albert GETs and Tesco's two read-only catalogue queries retry connection/timeouts
and HTTP 408/429/500/502/503/504, with at most four attempts. Backoff is exponential;
429/503 `Retry-After` seconds and HTTP dates are honored. Retry sleep is capped at
30 seconds per delay and 60 seconds total per request. If the server asks for a
longer wait, the refresh fails instead of retrying early. Diagnostics identify
the retailer, request, attempt, and delay. Other HTTP errors and malformed
JSON/schema data fail immediately. Tesco also leaves one second between
catalogue requests, including department boundaries.

These changes tolerate transient outages; they do not bypass blocks or make a
persistently unreachable endpoint healthy. Exhausted retries still exit nonzero
before that retailer's snapshot/history write. The orchestrator requires fresh
snapshots from every retailer before merging, so an old snapshot is not presented
as a successful refresh.

## Product pictures

Images are optional: missing or broken images never remove an offer. The builder
uses the standard Python urllib client identity, checks actual image decoding,
and caches thumbnails under a store/SKU/source-URL hash rather than reusing old
product-ID-only files. Valid normalized thumbnails are reused without lossy
re-encoding. Kupi image URLs are excluded, not used as a fallback.

Grouped views select an available decoded direct image independently of the
cheapest offer, and label it with its representative variant and store. Exact
pages use only their own retailer/SKU image; an unavailable image stays a neutral
placeholder. Dashboard and ranking pictures are requested only when visible,
not for every hidden category or off-screen row.

`site/image-coverage.json` and the build log report decoded direct images,
missing URLs, excluded Kupi URLs, failed downloads/decodes, and host-outage skips.
Coverage counts unique store/SKU/source identities, not offer rows. Image failures
are retried on the next build, with bounded parallel downloads and a per-build
transport-outage circuit breaker. Individual HTTP 404s do not block a whole host.

Albert's app image service has returned missing-file errors, and the runner has
also observed connection timeouts. No alternative exact-product image source is
verified. Billa's leaflet images can be extracted, but the experimental parser
cannot yet associate all prices, dates, units, conditions, and product regions
reliably. Do not enable that parser or replace a full snapshot with a few
hand-verified crops. The Billa daily source migration remains incomplete.

## Running

Use the repository virtualenv. The top-level runner works from any current
directory:

```bash
/home/mito/Projects/grocery-scraper/.venv/bin/python \
  /home/mito/Projects/grocery-scraper/run.py
```

Individual scrapers remain available:

```bash
uv run --no-dev python -m scrapers.albert
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
