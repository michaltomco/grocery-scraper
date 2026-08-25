import csv
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


LOCAL_TIMEZONE = ZoneInfo("Europe/Prague")
ROOT = Path(__file__).resolve().parent.parent
HISTORY_CSV = ROOT / "history.csv"
KUPI_BASE_URL = "https://www.kupi.cz"
KUPI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
}

# Top-level Kupi categories that represent grocery/food products. Keep the
# Kupi-visible Czech labels so the website filter mirrors the source taxonomy.
KUPI_FOOD_CATEGORIES = (
    ("Ovoce a zelenina", "ovoce-a-zelenina"),
    ("Pečivo", "pecivo"),
    ("Mléčné výrobky a vejce", "mlecne-vyrobky-a-vejce"),
    ("Maso, uzeniny a ryby", "maso-drubez-a-ryby"),
    ("Lahůdky", "lahudky"),
    ("Konzervy", "konzervy"),
    ("Mražené a instantní potraviny", "mrazene-a-instantni-potraviny"),
    ("Nealko nápoje", "nealko-napoje"),
    ("Vaření a pečení", "vareni-a-peceni"),
    ("Sladkosti a slané snacky", "sladkosti-a-slane-snacky"),
    ("Zdravá výživa", "zdrava-vyziva"),
    ("Alkohol", "alkohol"),
)

FIELDNAMES = [
    "store",
    "category",
    "product_id",
    "product_name",
    "canonical_product_name",
    "price",
    "old_price",
    "currency",
    "unit_price",
    "price_per_kg",
    "price_per_piece",
    "loyalty_required",
    "loyalty_program",
    "loyalty_price",
    "discount_label",
    "date_range",
    "url",
    "image_url",
    "scraped_at",
]

CANONICAL_WORDS = {
    "avokádo": "avokado",
    "avokado": "avokado",
    "avocado": "avokado",
    "ananas": "ananas",
    "banan": "banany",
    "banany": "banany",
    "boruvka": "boruvky",
    "boruvky": "boruvky",
    "brambor": "brambory",
    "brambory": "brambory",
    "broskev": "broskve",
    "broskve": "broskve",
    "cuketa": "cuketa",
    "cukety": "cuketa",
    "hrozny": "hrozny",
    "jablka": "jablka",
    "jablko": "jablka",
    "jahoda": "jahody",
    "jahody": "jahody",
    "kedlubna": "kedlubna",
    "kedlubny": "kedlubna",
    "kiwi": "kiwi",
    "kvetak": "kvetak",
    "mandarinka": "mandarinky",
    "mandarinky": "mandarinky",
    "meloun": "meloun",
    "melouny": "meloun",
    "merunka": "merunky",
    "merunky": "merunky",
    "mrkev": "mrkev",
    "nektarinka": "nektarinky",
    "nektarinky": "nektarinky",
    "okurka": "okurka",
    "okurky": "okurka",
    "paprika": "paprika",
    "papriky": "paprika",
    "rajce": "rajcata",
    "rajcata": "rajcata",
    "redkvicky": "redkvicky",
    "tresne": "tresne",
    "tresen": "tresne",
    "zampiony": "zampiony",
    "zeli": "zeli",
    "dyne": "dyne_hokkaido",
}

DESCRIPTOR_WORDS = {
    "bile",
    "bila",
    "bily",
    "bezsemenne",
    "cervena",
    "cervene",
    "cherry",
    "kerikova",
    "delicious",
    "golden",
    "hokkaido",
    "ceska",
    "farma",
    "hadovka",
    "kerikova",
    "konzumni",
    "nakladacky",
    "rane",
    "salatova",
    "svazek",
    "waikiki",
    "vodni",
    "zlute",
}

STORE_WORDS = {
    "albert",
    "billa",
    "bonvia",
    "lidl",
    "tesco",
}

# Single source of truth for per-store presentation metadata. The brand-colored
# logo is derived from (color, initial) so there is exactly one place to edit
# when a store is added or recolored. `loyalty_program` mirrors the value each
# scraper passes to KupiStoreConfig.
STORE_BRAND = {
    "Lidl": {"color": "#facc15", "initial": "L", "loyalty_program": "Lidl Plus"},
    "Tesco": {"color": "#f87171", "initial": "T", "loyalty_program": "Clubcard"},
    "Albert": {"color": "#60a5fa", "initial": "A", "loyalty_program": "Můj Albert"},
    "Billa": {"color": "#cc1f2c", "initial": "B", "loyalty_program": "Billa Club"},
}


def store_logo(store: str) -> str:
    """Return a small brand-colored SVG badge (letter on store color)."""
    brand = STORE_BRAND.get(store)
    if not brand:
        return ""
    color = brand["color"]
    initial = brand["initial"]
    return (
        f'<svg class="logo" viewBox="0 0 24 24" role="img" aria-label="{store}">'
        f'<rect x="2" y="2" width="20" height="20" rx="5" fill="{color}"/>'
        f'<text x="12" y="16" font-size="12" font-weight="700" text-anchor="middle" fill="#0f172a">'
        f"{initial}</text></svg>"
    )


def store_color(store: str) -> str:
    return STORE_BRAND.get(store, {}).get("color", "#64748b")


def store_initial(store: str) -> str:
    return STORE_BRAND.get(store, {}).get("initial", store[:1].upper())

CZECH_MONTHS = {
    "ledna": 1,
    "února": 2,
    "března": 3,
    "dubna": 4,
    "května": 5,
    "června": 6,
    "července": 7,
    "srpna": 8,
    "září": 9,
    "října": 10,
    "listopadu": 11,
    "prosince": 12,
}


@dataclass(frozen=True)
class KupiStoreConfig:
    store: str
    url: str
    csv_path: Path
    store_location: str
    loyalty_program: str
    category: str = "Ovoce a zelenina"


def category_configs(config: KupiStoreConfig) -> list[KupiStoreConfig]:
    """Expand one store config into Kupi's top-level grocery category URLs."""
    store_slug = config.url.rstrip("/").rsplit("/", 1)[-1]
    return [
        replace(
            config,
            url=f"{KUPI_BASE_URL}/slevy/{slug}/{store_slug}",
            category=label,
        )
        for label, slug in KUPI_FOOD_CATEGORIES
    ]


def today_timestamp() -> str:
    return datetime.now(LOCAL_TIMEZONE).isoformat(timespec="seconds")


def format_date_range(start_date: str, end_date: str) -> str:
    if start_date and end_date:
        return f"{start_date} - {end_date}"
    return start_date or end_date


def strip_accents(text: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )


def canonical_product_name(product_name: str) -> str:
    text = strip_accents(product_name.lower())
    text = re.sub(r"\b\d+(?:[,.]\d+)?\s*(?:kg|g|ks|ml|l)\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)

    # A preserved fruit is not equivalent to its fresh-produce counterpart.
    # Keep compotes as their own products so, for example, Giana strawberry
    # compote never appears in the fresh-strawberries comparison.
    if "kompot" in text.split():
        return "_".join(word for word in text.split() if not word.isdigit())

    canonical_words = []
    descriptors = []
    for word in text.split():
        if word in STORE_WORDS or word.isdigit():
            continue
        if word in CANONICAL_WORDS:
            canonical_words.append(CANONICAL_WORDS[word])
        elif word in DESCRIPTOR_WORDS:
            descriptors.append(word)

    unique_canonical_words = list(dict.fromkeys(canonical_words))
    if not unique_canonical_words:
        return "_".join(
            word for word in text.split() if word not in STORE_WORDS and not word.isdigit()
        )

    if unique_canonical_words == ["rajcata"]:
        if "cherry" in descriptors:
            return "rajcata_cherry"
        if "kerikova" in descriptors:
            return "rajcata_cherry"

    if unique_canonical_words == ["meloun"] and "vodni" in descriptors:
        return "meloun_vodni"

    if unique_canonical_words == ["zeli"]:
        # White and red fermented cabbage are distinct products. Keeping the
        # colour in the canonical key prevents their prices and exact-SKU links
        # from being mixed on the shared product page.
        if "cervene" in descriptors:
            return "zeli_cervene"
        if "bile" in descriptors or "bily" in descriptors:
            return "zeli"

    if unique_canonical_words == ["paprika"]:
        if "cervena" in descriptors:
            return "paprika_cervena"
        # White and red sweet peppers share the generic paprika category.
        if "bila" in descriptors or "bile" in descriptors or "bily" in descriptors:
            return "paprika"

    if unique_canonical_words == ["avokado"]:
        # "Avokádo" can be a flavour or brand of a prepared food (e.g. marináda,
        # omáčka) rather than the fresh fruit. When it is, keep the prepared
        # product as its own canonical name so it never merges into the avocado
        # nutrition/price row. Match on word stems so singular/plural and minor
        # spelling variants (omáčka/omáčky, marináda/marinády) all separate.
        if any(
            word.startswith(stem)
            for word in text.split()
            for stem in ("marinad", "omack", "dresink", "dip")
        ):
            return "_".join(
                word
                for word in text.split()
                if word not in STORE_WORDS and not word.isdigit()
            )
        # Scan the processed (accent-stripped, normalized) text directly for
        # subcategory signals — these words are not in DESCRIPTOR_WORDS and
        # would otherwise be lost.  Substring matching handles variants like
        # "nature's_promise" → "natures_promise", "zrelé" → "zrele", etc.
        text_lower = text.lower()
        is_bio = any(
            needle in text_lower
            for needle in ("bio", "organic", "natur", "nature", "nature_s_promise")
        )
        is_ready = any(
            needle in text_lower
            for needle in (
                "ready_to_eat", "ready_to_ripen", "readytoeat", "readytori",
                "zrno", "zrna", "zrel", "zren", "ripe", "ready",
            )
        )
        if is_bio and is_ready:
            return "avokado_ready_to_eat_bio"
        if is_bio:
            return "avokado_bio"
        if is_ready:
            return "avokado_ready_to_eat"
        return "avokado"

    return "_".join(unique_canonical_words)


def parse_number(text: str) -> float | None:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def normalize_unit_price(unit_price: str) -> dict[str, float | str]:
    normalized = {"price_per_kg": "", "price_per_piece": ""}
    text = " ".join(unit_price.replace("\xa0", " ").split()).lower()
    match = re.search(
        r"(\d+(?:[,.]\d+)?)\s*kč\s*/\s*(\d+(?:[,.]\d+)?)?\s*(kg|g|ks)",
        text,
    )
    if not match:
        return normalized

    price = parse_number(match.group(1))
    quantity = parse_number(match.group(2) or "1")
    unit = match.group(3)
    if price is None or quantity is None or quantity == 0:
        return normalized

    if unit == "kg":
        normalized["price_per_kg"] = round(price / quantity, 2)
    elif unit == "g":
        normalized["price_per_kg"] = round(price * 1000 / quantity, 2)
    elif unit == "ks":
        normalized["price_per_piece"] = round(price / quantity, 2)

    return normalized


def explicit_package_weight_grams(product_name: str) -> float | None:
    """Read a package weight only when it is the trailing name quantity."""
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(kg|g)\s*$", product_name or "", re.IGNORECASE)
    if not match:
        return None
    value = parse_number(match.group(1))
    if value is None or value <= 0:
        return None
    return value * 1000 if match.group(2).lower() == "kg" else value


def normalize_offer_unit_price(unit_price: str, product_name: str, price: float | str) -> dict[str, float | str]:
    """Prefer an explicit trailing package weight over a bad source unit price."""
    normalized = normalize_unit_price(unit_price)
    package_grams = explicit_package_weight_grams(product_name)
    package_price = parse_number(str(price)) if price not in (None, "") else None
    if package_grams is None or package_price is None:
        return normalized
    expected = round(package_price * 1000 / package_grams, 2)
    source = normalized.get("price_per_kg")
    if source in ("", None) or abs(float(source) - expected) > 0.2:
        normalized["price_per_kg"] = expected
        normalized["price_per_piece"] = ""
    return normalized


def clean_text(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def parse_czech_price(text: str) -> float | str:
    match = re.search(r"(\d+(?:[,.]\d+)?)\s*Kč", clean_text(text))
    if not match:
        return ""

    return float(match.group(1).replace(",", "."))


def parse_percentage(text: str) -> float | str:
    match = re.search(r"[–-]\s*(\d+(?:[,.]\d+)?)\s*%", clean_text(text))
    if not match:
        return ""

    return float(match.group(1).replace(",", "."))


def estimate_old_price(price: float | str, discount_percent: float | str) -> float | str:
    if not price or not discount_percent or discount_percent >= 100:
        return ""

    return round(price / (1 - discount_percent / 100), 2)


def parse_partial_date(text: str, today: date) -> str:
    normalized = clean_text(text).lower()
    match = re.search(
        r"(?:po|út|st|čt|pá|so|ne)?\s*(\d{1,2})\.\s*(\d{1,2})\.", normalized
    )
    if not match:
        match = re.search(r"(\d{1,2})\.\s*([a-zá-ž]+)", normalized)
        if not match:
            return ""

        day = int(match.group(1))
        month = CZECH_MONTHS.get(match.group(2), 0)
    else:
        day = int(match.group(1))
        month = int(match.group(2))

    if not month:
        return ""

    parsed = date(today.year, month, day)
    if parsed < today - timedelta(days=180):
        parsed = date(today.year + 1, month, day)
    return parsed.isoformat()


def parse_validity(text: str) -> tuple[str, str]:
    today = datetime.now(LOCAL_TIMEZONE).date()
    normalized = clean_text(text).lower()

    if "dnes končí" in normalized:
        return "", today.isoformat()
    if "zítra končí" in normalized:
        # "ends tomorrow": still valid today, so start = today, end = tomorrow.
        return today.isoformat(), (today + timedelta(days=1)).isoformat()

    if "platí do" in normalized:
        # "valid until <date>": the offer is active now, so start = today.
        return today.isoformat(), parse_partial_date(normalized, today)

    parts = [part.strip() for part in re.split(r"\s+[–-]\s+", normalized, maxsplit=1)]
    if len(parts) == 2:
        return parse_partial_date(parts[0], today), parse_partial_date(parts[1], today)

    return today.isoformat(), parse_partial_date(normalized, today)


def number(value: str) -> float | None:
    """Parse a Czech-formatted number (comma decimal) into float, or None."""
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def is_true(value: str) -> bool:
    """Interpret a CSV boolean-ish string as True."""
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def parsed_date_range(date_range: str) -> tuple[str, str]:
    """Return (start_iso, end_iso) from a date_range like '2026-08-04' or
    '2026-08-06 - 2026-08-09'. Missing end defaults to the start."""
    if not date_range:
        return "", ""
    if " - " in date_range:
        start, end = date_range.split(" - ", 1)
        return start.strip(), end.strip()
    return date_range.strip(), date_range.strip()


def is_active_offer(date_range: str, today: date, stale_horizon_days: int = 14) -> bool:
    """True when a discount is still relevant: its end date is today or later
    and its start is not hopelessly stale (within `stale_horizon_days` ahead).
    A missing/unparseable end date is treated as active to avoid dropping rows
    on bad data. Shared by the site builder, the price report, and the monitor
    so the "what's current" rule lives in exactly one place.
    """
    start_iso, end_iso = parsed_date_range(date_range)
    try:
        end = date.fromisoformat(end_iso) if end_iso else None
    except ValueError:
        end = None
    try:
        start = date.fromisoformat(start_iso) if start_iso else None
    except ValueError:
        start = None
    if end is None:
        return True
    stale_horizon = today + timedelta(days=stale_horizon_days)
    return end >= today and (start is None or start <= stale_horizon)


def kupi_product_name(wrap) -> str:
    title = wrap.select_one(".product_name h2 a[title]")
    amount = wrap.select_one(".product_name h2 .nowrap")
    parts = []
    if title:
        parts.append(clean_text(title["title"]))
    if amount:
        amount_text = clean_text(amount.get_text(" ", strip=True))
        if amount_text:
            parts.append(amount_text)

    return " ".join(parts)


def kupi_product_url(wrap, fallback_url: str) -> str:
    link = wrap.select_one(".product_name h2 a[href], .product_image a[href]")
    return urljoin(KUPI_BASE_URL, link["href"]) if link else fallback_url


def kupi_product_lookup(soup: BeautifulSoup, fallback_url: str) -> dict[str, dict[str, str]]:
    products = {}
    for wrap in soup.select(".product--wrap[data-product-id]"):
        product_id = wrap["data-product-id"]
        img = wrap.select_one(".product_image img")
        image_url = ""
        if img:
            # kupi.cz lazy-loads: real URL is in data-src, src is a placeholder
            raw = img.get("data-src") or img.get("src") or ""
            image_url = str(raw)
        products[product_id] = {
            "name": kupi_product_name(wrap),
            "url": kupi_product_url(wrap, fallback_url),
            "image": urljoin(KUPI_BASE_URL, image_url) if image_url else "",
        }

    return products


def kupi_shop_name(row) -> str:
    link = row.select_one(".discounts_shop_name a")
    return clean_text(link.get_text(" ", strip=True)) if link else ""


def kupi_row_url(row, fallback_url: str) -> str:
    leaflet_link = row.select_one("a.btn_link_leaflet[href]")
    if leaflet_link:
        return urljoin(KUPI_BASE_URL, leaflet_link["href"])

    product_link = row.select_one("a.product_link_history[href]")
    return urljoin(KUPI_BASE_URL, product_link["href"]) if product_link else fallback_url


def extract_kupi_discount(
    row,
    products: dict[str, dict[str, str]],
    scraped_at: str,
    config: KupiStoreConfig,
) -> dict:
    product_id = row.get("data-product", "")
    product = products.get(product_id, {})
    product_name = product.get("name") or row.get("data-product", "")
    row_text = clean_text(row.get_text(" ", strip=True))
    price_tag = row.select_one(".discount_price_value")
    price = parse_czech_price(price_tag.get_text(" ", strip=True) if price_tag else "")
    discount_tag = row.select_one(".discount_percentage")
    discount_percent = parse_percentage(
        discount_tag.get_text(" ", strip=True) if discount_tag else ""
    )
    old_price = estimate_old_price(price, discount_percent)
    amount = clean_text(
        row.select_one(".discount_amount").get_text(" ", strip=True)
        if row.select_one(".discount_amount")
        else ""
    ).lstrip("/")
    unit_price = clean_text(
        row.select_one(".price_per_unit").get_text(" ", strip=True)
        if row.select_one(".price_per_unit")
        else ""
    ) or amount
    normalized_price = normalize_offer_unit_price(unit_price, product_name, price)
    validity = clean_text(
        row.select_one(".discounts_validity").get_text(" ", strip=True)
        if row.select_one(".discounts_validity")
        else ""
    )
    start_date, end_date = parse_validity(validity)
    loyalty_required = "Platí pro členy klubu" in row_text
    discount_label = " ".join(
        part
        for part in [
            f"-{discount_percent:g}%" if discount_percent else "",
            config.loyalty_program if loyalty_required else "",
            validity,
        ]
        if part
    )

    return {
        "store": config.store,
        "category": config.category,
        "product_id": product_id,
        "product_name": product_name,
        "canonical_product_name": canonical_product_name(product_name),
        "price": price,
        "old_price": old_price,
        "currency": "Kč",
        "unit_price": unit_price,
        "price_per_kg": normalized_price["price_per_kg"],
        "price_per_piece": normalized_price["price_per_piece"],
        "loyalty_required": loyalty_required,
        "loyalty_program": config.loyalty_program if loyalty_required else "",
        "loyalty_price": price if loyalty_required else "",
        "discount_label": discount_label,
        "date_range": format_date_range(start_date, end_date),
        "url": kupi_row_url(row, config.url),
        "image_url": product.get("image", ""),
        "scraped_at": scraped_at,
    }


def fetch_kupi_html(url: str) -> str:
    response = requests.get(url, headers=KUPI_HEADERS, timeout=20)
    response.raise_for_status()
    return response.text


def extract_kupi_products(
    html: str, config: KupiStoreConfig, seen_discount_ids: set | None = None
) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products = kupi_product_lookup(soup, config.url)
    scraped_at = today_timestamp()
    rows = []
    if seen_discount_ids is None:
        seen_discount_ids = set()

    for row in soup.select(".discount_row[data-product][data-discount]"):
        discount_id = row["data-discount"]
        product_id = row.get("data-product", "")
        dedupe_key = (product_id, discount_id)
        if (
            dedupe_key in seen_discount_ids
            # kupi.cz lists the shop as e.g. "Albert Supermarket" /
            # "Albert Hypermarket", so match by containment, not exact equality.
            or config.store.lower() not in kupi_shop_name(row).lower()
        ):
            continue

        seen_discount_ids.add(dedupe_key)
        rows.append(extract_kupi_discount(row, products, scraped_at, config))

    return rows


def _with_page_param(url: str, page: int) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}page={page}"


def fetch_kupi_products(config: KupiStoreConfig) -> list[dict]:
    """Fetch every page of the Kupi category listing.

    Kupi paginates discounted products (the category page only shows the first
    page), so a single request silently misses everything on later pages. We
    walk `?page=N` until a page yields no new discounts, with a hard cap to
    avoid looping if pagination wraps.
    """
    all_rows: list[dict] = []
    seen_discount_ids: set = set()
    for page in range(1, 16):
        url = config.url if page == 1 else _with_page_param(config.url, page)
        try:
            html = fetch_kupi_html(url)
        except requests.RequestException as error:
            if page == 1:
                raise  # caller's handler reports the failure and aborts
            print(f"  stopped at page {page}: {error}")
            break

        page_rows = extract_kupi_products(html, config, seen_discount_ids)
        if not page_rows:
            break
        all_rows.extend(page_rows)
        print(f"  page {page}: +{len(page_rows)} (total {len(all_rows)})")

    return all_rows


def print_products_by_date(products: list[dict]) -> None:
    products_by_date = {}
    for product in products:
        products_by_date.setdefault(product["date_range"] or "No date", []).append(product)

    for date_range, date_products in products_by_date.items():
        print(f"\n{date_range}")
        for product in date_products:
            label = (
                f" ({product['loyalty_program']})"
                if product["loyalty_required"]
                else ""
            )
            print(
                f"{product['product_name']} -- {product['price']} "
                f"{product['currency']}{label} -- {product['unit_price']}"
            )


def run_kupi_scraper(config: KupiStoreConfig) -> None:
    try:
        products = fetch_kupi_products(config)
    except requests.RequestException as error:
        print(f"Could not fetch {config.store} discounts from Kupi.cz: {error}")
        return

    print(f"Found {len(products)} products")
    if not products:
        # A transient Kupi response or selector change must not erase the last
        # known-good store snapshot and make the site look like the store has no
        # produce offers. Keep the existing CSV for the next merge/website build.
        print(f"No {config.store} products returned; keeping existing snapshot unchanged.")
        return
    print_products_by_date(products)
    write_csv(config.csv_path, products)
    append_history(HISTORY_CSV, products)


def run_kupi_food_scraper(config: KupiStoreConfig) -> None:
    """Scrape every configured Kupi grocery category for one store."""
    products: list[dict] = []
    for category_config in category_configs(config):
        try:
            category_products = fetch_kupi_products(category_config)
        except requests.RequestException as error:
            print(
                f"Could not fetch {category_config.category} for {config.store} from Kupi.cz: {error}"
            )
            continue
        print(f"{category_config.category}: found {len(category_products)} products")
        products.extend(category_products)

    if not products:
        print(f"No {config.store} grocery products returned; keeping existing snapshot unchanged.")
        return

    # Kupi may surface an item under more than one top-level category. Keep one
    # exact category-tagged offer instead of duplicating it in the store CSV.
    deduped: dict[tuple[str, ...], dict] = {}
    for product in products:
        key = tuple(
            str(product.get(field, ""))
            for field in ("store", "category", "product_id", "price", "unit_price", "date_range", "url")
        )
        deduped[key] = product
    snapshot = list(deduped.values())
    print_products_by_date(snapshot)
    write_csv(config.csv_path, snapshot)
    append_history(HISTORY_CSV, snapshot)


def write_csv(path: str | Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: str | Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def history_key(row: dict) -> tuple[str, ...]:
    # Include scraped_at so a genuine per-day re-scrape is never treated as a
    # duplicate of an earlier day's row (same product/price/range). Without it,
    # a store that re-scrapes today would be dropped against yesterday's identical
    # key, leaving the newest run with no rows for that store.
    return tuple(
        row.get(field, "")
        for field in ("store", "category", "product_id", "date_range", "price", "unit_price", "scraped_at")
    )


def append_history(path: str | Path, new_rows: list[dict]) -> list[dict]:
    path = Path(path)
    existing = read_csv(path) if path.exists() else []
    rows = [{field: str(row.get(field, "")) for field in FIELDNAMES} for row in existing]
    seen: dict[tuple, dict] = {history_key(row): row for row in rows}
    for row in new_rows:
        normalized = {field: str(row.get(field, "")) for field in FIELDNAMES}
        key = history_key(normalized)
        if key not in seen:
            rows.append(normalized)
            seen[key] = normalized
        else:
            # Keep the richer row: prefer one that has an image_url so a
            # later re-scrape backfills images instead of being dropped.
            if normalized.get("image_url") and not seen[key].get("image_url"):
                idx = next(i for i, r in enumerate(rows) if history_key(r) == key)
                rows[idx] = normalized
                seen[key] = normalized
    rows.sort(key=lambda row: (row["store"], row["date_range"], row["product_name"], row["price"]))
    write_csv(path, rows)
    return rows


def merge_csvs(input_paths: list[str | Path], output_path: str | Path) -> list[dict]:
    rows = []
    seen = set()

    for input_path in input_paths:
        for row in read_csv(input_path):
            normalized_row = {field: row.get(field, "") for field in FIELDNAMES}
            dedupe_key = (
                normalized_row["store"],
                normalized_row["category"],
                normalized_row["product_id"],
                normalized_row["price"],
                normalized_row["unit_price"],
                normalized_row["date_range"],
                normalized_row["url"],
            )
            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)
            rows.append(normalized_row)

    rows.sort(
        key=lambda row: (
            row["canonical_product_name"],
            row["store"],
            row["date_range"],
            row["product_name"],
            row["price"],
        )
    )
    write_csv(output_path, rows)
    return rows
