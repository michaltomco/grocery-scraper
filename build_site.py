"""Generate a self-contained HTML dashboard from the scraped grocery data.

Reads history.csv (cumulative) and produces index.html:
  - summary header (last run, # items, # stores)
  - sortable table: one row per product; Store and Price are separate columns
    with each store stacked vertically; clickable legend toggles stores;
    plus a price-trend sparkline (populates as more daily runs accumulate)
Depends on Pillow to normalize product images (white square canvas) so every
thumbnail shares the same aspect ratio without cropping. Output is a single
file with inline CSS/JS.
"""
import csv
import re
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from scrapers.common import read_csv, canonical_product_name
except ModuleNotFoundError:
    from common import read_csv, canonical_product_name
from nutrition import get_many

ROOT = Path(__file__).resolve().parent
HISTORY_CSV = ROOT / "history.csv"
SITE_DIR = ROOT / "site"
IMG_DIR = SITE_DIR / "img"
PRODUCTS_DIR = SITE_DIR / "products"
INDEX_HTML = SITE_DIR / "index.html"

# Download each product image exactly once, keyed by product_id. Already-cached
# files are never re-fetched. Each image is normalized onto a square white
# canvas (largest-side scaled to 220px, centered, no cropping) so every
# thumbnail shares one aspect ratio and the white padding blends with the
# product's own white background. Returns a web path relative to the site root.
THUMB = 220  # px, size of the normalized square canvas


def normalize_image_file(path: Path) -> bool:
    """Normalize an existing image file onto a white square canvas in place.
    Returns True if it was processed (or already square), False on error."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            if im.size == (THUMB, THUMB):
                return True
            im = im.convert("RGB")
            im.thumbnail((THUMB, THUMB), Image.LANCZOS)
            canvas = Image.new("RGB", (THUMB, THUMB), (255, 255, 255))
            canvas.paste(im, ((THUMB - im.width) // 2, (THUMB - im.height) // 2))
            canvas.save(path, "JPEG", quality=90)
        return True
    except Exception:
        return False


def cache_image(product_id: str, image_url: str) -> str:
    if not image_url:
        return ""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(image_url.split("?")[0]).suffix or ".jpg"
    dest = IMG_DIR / f"{product_id}{ext}"
    # If already cached, re-normalize the cached file itself (idempotent,
    # no re-download). Otherwise fetch to a temp raw file first.
    src = dest if dest.exists() else None
    raw = IMG_DIR / f".raw_{product_id}{ext}"
    try:
        if src is None:
            if not raw.exists():
                req = urllib.request.Request(
                    image_url,
                    headers={"User-Agent": "Mozilla/5.0 (grocery-scraper)"},
                )
                with urllib.request.urlopen(req, timeout=20) as resp, raw.open("wb") as out:
                    out.write(resp.read())
            src = raw
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB, THUMB), Image.LANCZOS)
            canvas = Image.new("RGB", (THUMB, THUMB), (255, 255, 255))
            canvas.paste(im, ((THUMB - im.width) // 2, (THUMB - im.height) // 2))
            canvas.save(dest, "JPEG", quality=90)
    except Exception:
        return ""
    finally:
        if raw.exists():
            try:
                raw.unlink()
            except OSError:
                pass
    return f"img/{dest.name}"


def number(value: str) -> float | None:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def normalized(row: dict) -> tuple[float | None, str]:
    kg = number(row.get("price_per_kg", ""))
    if kg is not None:
        return kg, "kg"
    pc = number(row.get("price_per_piece", ""))
    if pc is not None:
        return pc, "ks"
    # Fallback: some scraped rows only carry a generic `price` (no per_kg/per_piece).
    # Infer the unit from the product name ("1 kg" -> kg, otherwise per piece).
    generic = number(row.get("price", ""))
    if generic is not None:
        name = (row.get("product_name") or row.get("canonical_product_name") or "")
        unit = "kg" if re.search(r"\b\d+(?:[.,]\d+)?\s*kg\b", name, re.IGNORECASE) else "ks"
        return generic, unit
    return None, ""


def parse_ts(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return datetime.min


def esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


STORE_WORDS_RE = re.compile(r"\b(albert|lidl|tesco)\b", re.IGNORECASE)
QTY_RE = re.compile(r"\b\d+(?:[,.]\d+)?\s*(?:kg|g|ks|l)\b", re.IGNORECASE)


def pretty_name(name: str) -> str:
    """Make the raw Czech product name readable: strip store words and trailing
    quantity, collapse whitespace, capitalize the first letter."""
    n = STORE_WORDS_RE.sub("", name)
    n = QTY_RE.sub("", n)
    n = " ".join(n.split()).strip()
    if n:
        n = n[0].upper() + n[1:]
    return n or name


GENERAL_DISPLAY_NAMES = {
    "mrkev": "Mrkev",
    "brambory": "Brambory",
    "cibule": "Cibule",
    "paprika": "Paprika",
    "rajcata": "Rajčata",
    "okurka": "Okurka",
    "jablka": "Jablka",
    "banany": "Banány",
    "hrozny": "Hrozny",
    "jarni_cibule_svazek": "Jarní cibule",
}

RDA_VALUES = {
    "Vitamin A": (900, "µg"), "Vitamin B1": (1.2, "mg"), "Vitamin B2": (1.3, "mg"),
    "Vitamin B3": (16, "mg"), "Vitamin B5": (5, "mg"), "Vitamin B6": (1.3, "mg"),
    "Vitamin B7 (Biotin)": (30, "µg"), "Vitamin B9 (Folate)": (400, "µg"),
    "Vitamin B9": (400, "µg"),
    "Vitamin B12": (2.4, "µg"), "Vitamin C": (90, "mg"), "Vitamin D": (15, "µg"),
    "Vitamin E": (15, "mg"), "Vitamin K": (120, "µg"),
    "Calcium": (1300, "mg"), "Iron": (18, "mg"), "Magnesium": (420, "mg"),
    "Phosphorus": (1250, "mg"), "Potassium": (4700, "mg"), "Sodium": (2300, "mg"),
    "Zinc": (11, "mg"), "Copper": (0.9, "mg"), "Manganese": (2.3, "mg"),
    "Selenium": (55, "µg"),
}


def rda_percent(label: str, value, unit: str) -> int | None:
    label = label.replace("Vitamin B9 (Folate)", "Vitamin B9")
    if label not in RDA_VALUES or value in (None, 0):
        return None
    amount, target_unit = float(value), RDA_VALUES[label][1]
    normalized_unit = unit.lower().replace("μ", "µ")
    if normalized_unit in {"ug", "µg", "mcg"} and target_unit == "mg":
        amount /= 1000
    elif normalized_unit == "g" and target_unit == "mg":
        amount *= 1000
    elif normalized_unit in {"mg", "g"} and target_unit == "µg":
        amount = amount * (1_000_000 if normalized_unit == "g" else 1000)
    elif label == "Vitamin A" and normalized_unit in {"iu", "i.u."}:
        amount *= 0.3
    return round(amount / RDA_VALUES[label][0] * 100)


def display_name(product: str, raw_name: str) -> str:
    return GENERAL_DISPLAY_NAMES.get(product, pretty_name(raw_name))


def parse_range(date_range: str) -> tuple[str, str]:
    """Return (start_iso, end_iso) for a date_range like '2026-08-04' or
    '2026-08-06 - 2026-08-09'. Missing end defaults to the start."""
    if not date_range:
        return "", ""
    if " - " in date_range:
        start, end = date_range.split(" - ", 1)
        return start.strip(), end.strip()
    return date_range.strip(), date_range.strip()


def _iso(d: str):
    try:
        return date.fromisoformat(d.strip())
    except ValueError:
        return None


def fmt_cz(iso: str) -> str:
    """Format an ISO date or an ISO 'start - end' range as Czech D.M.YYYY
    (day first). For a same-year range the start year is omitted, e.g.
    '6.8 – 9.8.2026'. A single-day range collapses to one date, e.g.
    '5.8.2026'. Falls back to the raw string if not a parseable ISO date."""
    if not iso:
        return ""
    if " - " in iso:
        a, b = iso.split(" - ", 1)
        da, db = _iso(a), _iso(b)
        if da and db and da == db:
            return fmt_cz(a)
        if da and db and da.year == db.year:
            return f"{da.day}.{da.month} – {db.day}.{db.month}.{db.year}"
        return f"{fmt_cz(a)} – {fmt_cz(b)}"
    d = _iso(iso)
    if not d:
        return iso
    return f"{d.day}.{d.month}.{d.year}"


def fmt_md(iso: str) -> str:
    """Format an ISO date as Czech D.M (day first), no year. Used for the
    date-range picker labels, which only span a couple of weeks so the year
    is just noise."""
    d = _iso(iso)
    if not d:
        return iso
    return f"{d.day}.{d.month}"


# Czech 2-letter day-of-week abbreviations (Po/Út/St/Čt/Pá/So/Ne), Monday-first
# to match Python's datetime.weekday().
_DOW_CZ = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
# Single-letter glyphs for the tiny 13px squares (P=Po/So, Ú=Út, S=St/So, Č=Čt,
# P=Pá, N=Ne). Monday-first to match datetime.weekday().
_DOW1_CZ = ["P", "Ú", "S", "Č", "P", "S", "N"]


def fmt_md_dow(iso: str) -> str:
    """fmt_md plus a leading 2-letter Czech day-of-week, e.g. 'Po 5.8'."""
    d = _iso(iso)
    if not d:
        return iso
    return f"{_DOW_CZ[d.weekday()]} {d.day}.{d.month}"


def sparkline(series: list[tuple[datetime, float]]) -> str:
    """Return an inline SVG sparkline for a per-store price series."""
    if len(series) < 1:
        return ""
    pts = sorted(series, key=lambda p: p[0])
    vals = [p[1] for p in pts]
    w, h = 120, 28
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(pts)
    step = w / max(n - 1, 1)
    coords = []
    for i, v in enumerate(vals):
        x = i * step
        y = h - 3 - ((v - lo) / span) * (h - 6)
        coords.append((x, y))
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    # mark first/last
    last = coords[-1]
    return (
        f'<svg width="{w}" height="{h}" class="spark">'
        f'<polyline points="{path}" fill="none" stroke="#3b82f6" '
        f'stroke-width="1.5"/>'
        f'<circle cx="{last[0]:.1f}" cy="{last[1]:.1f}" r="2" fill="#1d4ed8"/>'
        f'</svg>'
    )


def write_product_pages(products: dict[str, list[dict]], nutrition: dict) -> None:
    """Write one static detail page per canonical produce item."""
    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
    product_keys = sorted(products)
    for product_index, product in enumerate(product_keys):
        entries = products[product]
        first = entries[0] if entries else {}
        pretty = display_name(product, first.get("raw_name", product))
        img_path = cache_image(first.get("product_id", product), first.get("image_url", ""))
        image_html = (
            f'<img class="hero" src="../{esc(img_path)}" alt="{esc(pretty)}">'
            if img_path else ""
        )
        nutrition_entry = nutrition.get(product, {})
        values = nutrition_entry.get("values", {})
        groups = {
            "Calories & macros": {
                "Calories", "Protein", "Carbs", "Fat", "Fiber", "Omega-3 fat", "Omega-6 fat",
            },
            "Vitamins": {
                "Vitamin A", "Vitamin B1", "Vitamin B2", "Vitamin B3", "Vitamin B5",
                "Vitamin B6", "Vitamin B7 (Biotin)", "Vitamin B9", "Vitamin B9 (Folate)", "Vitamin B12",
                "Vitamin C", "Vitamin D", "Vitamin E", "Vitamin K",
            },
            "Minerals": {
                "Calcium", "Iron", "Magnesium", "Phosphorus", "Potassium", "Zinc",
                "Copper", "Manganese", "Selenium", "Sodium",
            },
        }

        def nutrition_section(title: str, labels: set[str]) -> str:
            vitamin_order = {
                "Vitamin A": 1, "Vitamin B1": 2, "Vitamin B2": 3, "Vitamin B3": 4,
                "Vitamin B5": 5, "Vitamin B6": 6, "Vitamin B7 (Biotin)": 7,
                "Vitamin B9": 8, "Vitamin B9 (Folate)": 8, "Vitamin B12": 9, "Vitamin C": 10,
                "Vitamin D": 11, "Vitamin E": 12, "Vitamin K": 13,
            }
            ordered_values = sorted(
                ((label, value) for label, value in values.items() if label in labels),
                key=lambda item: vitamin_order.get(item[0], item[0]),
            )

            def value_html(label: str, value: dict) -> str:
                amount = value.get("value")
                if amount == 0:
                    return (
                        '<span class="nutrient-value">'
                        '<span class="mode-raw">–</span><span class="mode-rda">–</span>'
                        '<span class="mode-axis"><span class="axis"><span class="axis-fill" style="width:0;min-width:0"></span></span></span>'
                        '</span>'
                    )
                unit = esc(value.get("unit", ""))
                percentage = rda_percent(label, amount, value.get("unit", ""))
                raw = f"{esc(amount)} {unit}"
                if percentage is None:
                    return raw
                width = min(max(percentage, 0), 100)
                return (
                    f'<span class="nutrient-value">'
                    f'<span class="mode-raw">{raw}</span>'
                    f'<span class="mode-rda">{percentage}%</span>'
                    f'<span class="mode-axis"><span class="axis"><span class="axis-fill" style="width:{width}%"></span></span></span>'
                )

            rows = "".join(
                f'<tr><th>{esc((label.replace("Vitamin B9 (Folate)", "Vitamin B9").replace("Vitamin ", "").split(" (")[0]) if title == "Vitamins" else label)}</th><td>{value_html(label, value)}</td></tr>'
                for label, value in ordered_values
            )
            if not rows:
                rows = '<tr><td colspan="2" class="muted">No data available</td></tr>'
            return f'<section class="nutrition-group"><h2>{title}</h2><table>{rows}</table></section>'

        nutrition_sections = "".join(
            nutrition_section(title, labels) for title, labels in groups.items()
        )

        timeline_days = [date.today() + timedelta(days=i) for i in range(14)]
        day_letters = ["P", "Ú", "S", "Č", "P", "S", "N"]

        detail_store_colors = {
            "Lidl": "#facc15", "Tesco": "#f87171",
            "Albert": "#60a5fa", "Billa": "#cc1f2c",
        }
        store_initials = {"Lidl": "L", "Tesco": "T", "Albert": "A", "Billa": "B"}

        def store_chip(store: str) -> str:
            color = detail_store_colors.get(store, "var(--accent)")
            initial = store_initials.get(store, store[:1].upper())
            return f'<span class="store-chip" style="--store-color:{color}"><span class="store-mark">{esc(initial)}</span>{esc(store)}</span>'

        def detail_timeline(date_range: str, store: str) -> str:
            start, end = parse_range(date_range)
            start_date, end_date = _iso(start), _iso(end)
            if not start_date or not end_date:
                return '<span class="muted">No dates</span>'
            cells = []
            for day in timeline_days:
                active = start_date <= day <= end_date
                cells.append(
                    f'<span class="day{" active" if active else ""}" data-date="{day.isoformat()}" '
                    f'title="{esc(fmt_cz(day.isoformat()))}">{day_letters[day.weekday()]}</span>'
                )
            color = detail_store_colors.get(store, "var(--accent)")
            weeks, current = [], []
            for index, cell in enumerate(cells):
                if index > 0 and timeline_days[index].weekday() == 0:
                    weeks.append(f'<div class="wk">{"".join(current)}</div>')
                    current = []
                current.append(cell)
            if current:
                weeks.append(f'<div class="wk">{"".join(current)}</div>')
            return f'<div class="timeline" style="--store-color:{color}">' + "".join(weeks) + '</div>'

        discount_rows = "".join(
            f'<tr data-store="{esc(e.get("store", ""))}" data-start="{esc(parse_range(e.get("date_range", ""))[0])}" data-end="{esc(parse_range(e.get("date_range", ""))[1])}"><td>{store_chip(e.get("store", ""))}</td>'
            f'<td class="discount-price">{esc(e.get("val") if e.get("val") is not None else "-")} / {esc(e.get("unit", ""))}</td>'
            f'<td>{detail_timeline(e.get("date_range", ""), e.get("store", ""))}</td></tr>'
            for e in sorted(
                entries,
                key=lambda item: (
                    item.get("val") is None,
                    item.get("val") if item.get("val") is not None else float("inf"),
                    item.get("store", ""),
                ),
            )
        )
        source = nutrition_entry.get("source", "")
        source_html = f'<p class="muted">Source: {esc(source)}</p>' if source else ""
        previous_html = ""
        next_html = ""
        if product_index > 0:
            previous = product_keys[product_index - 1]
            previous_html = f'<a class="nav-button" href="{previous}.html" aria-label="Previous produce" title="Previous produce">&lt;</a>'
        if product_index + 1 < len(product_keys):
            following = product_keys[product_index + 1]
            next_html = f'<a class="nav-button" href="{following}.html" aria-label="Next produce" title="Next produce">&gt;</a>'
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(pretty)} · Grocery Prices</title>
<style>
:root {{ --bg:#1a1b26; --fg:#c0caf5; --muted:#565f89; --surface:#24283b; --border:#292e42; --accent:#7aa2f7; --track:#3b4261; --axis-track:#3b4261; --logo-ink:#0f172a; }}
:root[data-theme="light"] {{ --bg:#eff1f5; --fg:#4c4f69; --muted:#4c4f69; --surface:#e6e9ef; --border:#ccd0da; --accent:#1e66f5; --track:#9ca0b0; --axis-track:#a3a8b8; --logo-ink:#0f172a; }}
@media (prefers-color-scheme: light) {{ :root:not([data-theme="dark"]) {{ --bg:#eff1f5; --fg:#4c4f69; --muted:#4c4f69; --surface:#e6e9ef; --border:#ccd0da; --accent:#1e66f5; --axis-track:#a3a8b8; }} }}
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 900px; margin: 0 auto;
  padding: 24px; background: var(--bg); color: var(--fg); }}
a {{ color: var(--accent); }} .muted {{ color: var(--muted); }}
.hero {{ width: 180px; height: 180px; object-fit: contain; background: var(--surface); border-radius: 12px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin: 16px 0; }}
.nutrition-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
.nutrition-group {{ background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }}
.nutrition-group h2 {{ color: var(--accent); }}
.product-summary {{ display:grid; grid-template-columns:minmax(180px, 1fr) minmax(360px, 2fr); gap:16px; align-items:start; }}
.product-visual {{ min-width:0; }}
.product-summary > .card {{ margin-top:0; }}
.store-chip {{ display:inline-flex; align-items:center; gap:6px; font-weight:600; cursor:pointer; }}
.discount-price {{ font-weight:700; cursor:pointer; }}
.discount-row-off td {{ opacity:.3; }}
.muted td {{ opacity:.3; color:var(--muted); }}
.datedim td {{ opacity:1; color:var(--muted); }}
.muted .store-chip {{ opacity:1; color:var(--muted); }}
.datedim .store-chip, .datedim .discount-price {{ opacity:.28; color:var(--muted); }}
.muted .store-mark, .datedim .store-mark {{ opacity:.35; }}
.datedim .day.active {{ opacity:.28; }}
.store-chip.off {{ opacity:.35; }}
.muted .store-chip.off {{ opacity:1; }}
.store-mark {{ display:inline-flex; align-items:center; justify-content:center; width:22px; height:22px; border-radius:6px; background:var(--store-color); color:#0f172a; font-weight:700; font-size:.8rem; }}
.nutrition-heading {{ display:flex; justify-content:space-between; align-items:center; gap:12px; }}
.nutrition-mode {{ display:flex; border:1px solid var(--border); border-radius:999px; overflow:hidden; }}
.nutrition-mode button {{ border:0; padding:5px 9px; background:transparent; color:var(--fg); cursor:pointer; }}
.nutrition-mode button + button {{ border-left:1px solid var(--border); }}
.nutrition-mode button.active {{ background:var(--accent); color:var(--bg); font-weight:600; }}
.produce-nav {{ display:flex; gap:8px; position:relative; top:6px; }}
.title-row {{ display:flex; align-items:center; justify-content:space-between; gap:16px; }}
.nav-button {{ display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--border); border-radius:999px; padding:3px 8px; font-size:14px; line-height:1.2; font-weight:700; text-decoration:none; color:var(--fg); background:var(--surface); }}
.nav-button:hover {{ border-color:var(--accent); color:var(--accent); }}
.back-button {{ display:inline-block; border:1px solid var(--border); border-radius:999px; padding:6px 12px; text-decoration:none; color:var(--fg); background:var(--surface); }}
.back-button:hover {{ border-color:var(--accent); color:var(--accent); }}
.mode-rda, .mode-axis {{ display:none; }}
.mode-axis {{ width:135px; align-items:center; justify-content:flex-end; gap:5px; }}
.axis {{ display:inline-block; vertical-align:middle; width:100px; height:10px; margin-right:5px; background:var(--axis-track); border:0; border-radius:4px; overflow:hidden; }}
.axis-fill {{ display:block; min-width:3px; height:100%; background:var(--accent); border-radius:3px; }}
.axis-label {{ font-size:.8em; font-weight:600; color:var(--fg); }}
.timeline {{ display:flex; gap:8px; align-items:center; width:max-content; }}
.timeline .wk {{ display:flex; gap:3px; }}
.day {{ width:13px; height:13px; border-radius:3px; background:var(--track); opacity:.28;
  display:flex; align-items:center; justify-content:center; font-size:8px; line-height:1;
  font-weight:700; color:var(--logo-ink); cursor:pointer; user-select:none; }}
.day.active {{ background:var(--store-color, var(--accent)); opacity:1; color:#0f172a; border-color:var(--store-color, var(--accent)); }}
.day.sel {{ outline:2px solid var(--fg); outline-offset:1px; }}
.topbar {{ display:flex; justify-content:space-between; align-items:center; gap:16px; }}
.theme {{ display:flex; border:1px solid var(--border); border-radius:999px; overflow:hidden; }}
.theme button {{ border:0; padding:6px 10px; background:transparent; color:var(--fg); cursor:pointer; }}
.theme button + button {{ border-left:1px solid var(--border); }}
.theme button.active {{ background:var(--accent); color:var(--bg); font-weight:600; }}
@media (max-width: 700px) {{ .nutrition-grid, .product-summary {{ grid-template-columns: 1fr; }} }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid var(--border); }}
.nutrition-group th {{ text-align:left; width:50%; }}
.nutrition-group td {{ text-align:right; white-space:nowrap; }}
h1 {{ margin-bottom: 6px; }} h2 {{ margin-top: 0; font-size: 1rem; }}
</style></head><body>
<p><a class="back-button" href="../index.html">Back</a></p>
<div class="title-row"><h1>{esc(pretty)}</h1><div class="produce-nav">{previous_html}{next_html}</div></div>
<div class="product-summary"><div class="product-visual">{image_html}</div><div class="card"><h2>Current discounts</h2><table id="discountTable"><thead><tr><th>Store</th><th>Price</th><th>Discount days</th></tr></thead><tbody>{discount_rows}</tbody></table></div></div>
<div class="card"><div class="nutrition-heading"><h2>Nutrition per 100 g</h2><div class="nutrition-mode" id="nutritionMode"><button data-mode="axis">Axis</button><button data-mode="rda">RDA</button><button data-mode="raw">Raw</button></div></div><div class="nutrition-grid">{nutrition_sections}</div>{source_html}</div>
</body><script>
const key = 'grocery-theme';
function applyTheme(mode) {{
  if (mode === 'light' || mode === 'dark') document.documentElement.setAttribute('data-theme', mode);
  else document.documentElement.removeAttribute('data-theme');
}}
applyTheme(localStorage.getItem(key) || 'system');
const nutritionMode = document.getElementById('nutritionMode');
nutritionMode.querySelectorAll('button').forEach(button => button.onclick = () => {{
  const mode = button.dataset.mode;
  document.querySelectorAll('.mode-raw, .mode-rda, .mode-axis').forEach(item => item.style.display = 'none');
  document.querySelectorAll('.mode-' + mode).forEach(item => item.style.display = mode === 'axis' ? 'flex' : 'inline');
  nutritionMode.querySelectorAll('button').forEach(item => item.classList.toggle('active', item === button));
  localStorage.setItem('grocery-nutrition-mode', mode);
}});
nutritionMode.querySelector('[data-mode="' + (localStorage.getItem('grocery-nutrition-mode') || 'axis') + '"]').click();
const hidden = new Set();
const todayParts = "{date.today().isoformat()}".split("-").map(Number);
function offsetToIso(off) {{
  const d = new Date(Date.UTC(todayParts[0], todayParts[1] - 1, todayParts[2] + off));
  return `${{d.getUTCFullYear()}}-${{String(d.getUTCMonth()+1).padStart(2,'0')}}-${{String(d.getUTCDate()).padStart(2,'0')}}`;
}}
let rangeStart = offsetToIso(0), rangeEnd = offsetToIso(13);
function applyFilters() {{
  document.querySelectorAll('#discountTable .day').forEach(day => day.classList.toggle('sel', day.dataset.date === rangeStart || day.dataset.date === rangeEnd));
  document.querySelectorAll('#discountTable tr[data-store]').forEach(row => {{
    const storeMuted = hidden.has(row.dataset.store);
    const outside = row.dataset.start && row.dataset.end && (row.dataset.end < rangeStart || row.dataset.start > rangeEnd);
    row.classList.toggle('muted', storeMuted);
    row.classList.toggle('datedim', !storeMuted && outside);
  }});
}}
function setStoreHidden(store, hide) {{
  if (hide) hidden.add(store); else hidden.delete(store);
  document.querySelectorAll('.store-chip').forEach(chip => chip.classList.toggle('off', hidden.has(chip.closest('tr').dataset.store)));
  applyFilters();
}}
document.querySelectorAll('.store-chip').forEach(chip => chip.onclick = () => setStoreHidden(chip.closest('tr').dataset.store, !hidden.has(chip.closest('tr').dataset.store)));
document.querySelectorAll('.discount-price').forEach(price => price.onclick = () => {{
  const store = price.closest('tr').dataset.store;
  setStoreHidden(store, !hidden.has(store));
}});
let selecting = false, anchor = null;
document.querySelectorAll('#discountTable .day').forEach(day => {{
  day.addEventListener('pointerdown', event => {{
    event.preventDefault();
    if (rangeStart === rangeEnd && day.dataset.date === rangeStart) {{
      selecting = false; rangeStart = offsetToIso(0); rangeEnd = offsetToIso(13); applyFilters(); return;
    }}
    selecting = true; anchor = day.dataset.date; rangeStart = anchor; rangeEnd = anchor; applyFilters();
  }});
  day.addEventListener('pointerenter', () => {{ if (!selecting) return; rangeStart = anchor < day.dataset.date ? anchor : day.dataset.date; rangeEnd = anchor < day.dataset.date ? day.dataset.date : anchor; applyFilters(); }});
}});
document.addEventListener('pointerup', () => {{ selecting = false; }});
applyFilters();
</script></html>"""
        (PRODUCTS_DIR / f"{product}.html").write_text(page, encoding="utf-8")


def build() -> str:
    if not HISTORY_CSV.exists():
        rows = []
    else:
        # Normalize any already-cached images onto white square canvases, so every
        # thumbnail shares one aspect ratio (covers stale files whose product no
        # longer has an image_url in the latest run).
        if IMG_DIR.exists():
            for f in IMG_DIR.glob("*.*"):
                if f.name.startswith(".raw_"):
                    continue
                normalize_image_file(f)
        rows = read_csv(HISTORY_CSV)

    nutrition = get_many({canonical_product_name(r.get("product_name", "")) for r in rows})

    today_iso = date.today().isoformat()
    today_date = date.fromisoformat(today_iso)
    # A discount is still relevant if its end date is today or later. We also
    # tolerate flyers whose start is a few days in the future (Kupi publishes
    # "st 5. 8. – út 11. 8." offers several days early) so those count as
    # current too, but anything that ended long ago is dropped as stale.
    STALE_HORIZON = today_date + timedelta(days=14)

    all_scrape_dates = sorted(
        {r.get("scraped_at", "")[:10] for r in rows if r.get("scraped_at")},
        reverse=True,
    )
    last_run = all_scrape_dates[0] if all_scrape_dates else ""
    has_history = len(all_scrape_dates) > 1

    # ACTIVE-WINDOW model (replaces the old single-"current run" day):
    # show every discount that is still valid today — not just rows from the
    # one global latest scrape day. Stores finish their daily cron on different
    # calendar days, and Kupi flyers are forward-dated, so a product scraped a
    # few days ago with a range covering today is a current offer and must be
    # shown. For each (product, store) keep the still-active entry with the
    # *latest* end date (longest-valid offer wins).
    current_entries: dict[tuple[str, str], dict] = {}
    series: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    stores_seen: set[str] = set()
    for r in rows:
        store = r.get("store", "")
        # Re-canonicalize from the raw product name so that any recent
        # canonicalizer enhancements (e.g. avocado subcategories) take
        # effect even for rows whose CSV column predates the change.
        product = canonical_product_name(r.get("product_name", ""))
        stores_seen.add(store)
        val, unit = normalized(r)
        ts = parse_ts(r.get("scraped_at", ""))
        if val is not None:
            series[(product, store)].append((ts, val))
        # Is this discount still active (end date >= today, not hopelessly stale)?
        s, en = parse_range(r.get("date_range", ""))
        end = date.fromisoformat(en) if en else None
        start = date.fromisoformat(s) if s else None
        if end is None:
            active = True  # no parseable date → keep (avoid dropping on bad data)
        else:
            active = end >= today_date and (start is None or start <= STALE_HORIZON)
        if not active:
            continue
        # keep the most-valid (latest-ending) still-active entry per store
        lp = number(r.get("loyalty_price", ""))
        disp_val = val
        if r.get("loyalty_required", "").strip().lower() in {"true", "1", "yes", "y"} and lp is not None:
            disp_val = lp
        existing = current_entries.get((product, store))
        if existing is None or (end is not None and (existing["_end"] is None or end > existing["_end"])):
            current_entries[(product, store)] = {
                "product": product, "store": store,
                "val": disp_val if disp_val is not None else val,
                "unit": unit, "date_range": r.get("date_range", ""),
                "product_id": r.get("product_id", ""),
                "image_url": r.get("image_url", ""),
                "raw_name": r.get("product_name", product),
                "_end": end,
            }

    # Combined per-product trend: for each calendar day, the LOWEST price across
    # all stores (so the single product sparkline shows the best available deal
    # over time). Keyed by product -> sorted list of (datetime, float).
    day_min: dict[str, dict[date, float]] = defaultdict(dict)
    for (product, _store), pts in series.items():
        for ts, val in pts:
            d = ts.date()
            cur = day_min[product].get(d)
            if cur is None or val < cur:
                day_min[product][d] = val
    lowest_series: dict[str, list[tuple[datetime, float]]] = {}
    for product, dm in day_min.items():
        lowest_series[product] = sorted(
            [(datetime(d.year, d.month, d.day), v) for d, v in dm.items()],
            key=lambda p: p[0],
        )

    products: dict[str, list[dict]] = defaultdict(list)
    for (product, store), e in current_entries.items():
        products[product].append(e)
    write_product_pages(products, nutrition)

    # Store brand colors (not price-ranked): Lidl=yellow, Tesco=red, Albert=blue, Billa=red(#cc1f2c).
    STORE_COLOR = {"Lidl": "#facc15", "Tesco": "#f87171", "Albert": "#60a5fa", "Billa": "#cc1f2c"}
    # Small brand-colored logo per store (letter badge on brand color).
    STORE_LOGO = {
        "Lidl": '<svg class="logo" viewBox="0 0 24 24" role="img" aria-label="Lidl"><rect x="2" y="2" width="20" height="20" rx="5" fill="#facc15"/><text x="12" y="16" font-size="12" font-weight="700" text-anchor="middle" fill="#0f172a">L</text></svg>',
        "Tesco": '<svg class="logo" viewBox="0 0 24 24" role="img" aria-label="Tesco"><rect x="2" y="2" width="20" height="20" rx="5" fill="#f87171"/><text x="12" y="16" font-size="12" font-weight="700" text-anchor="middle" fill="#0f172a">T</text></svg>',
        "Albert": '<svg class="logo" viewBox="0 0 24 24" role="img" aria-label="Albert"><rect x="2" y="2" width="20" height="20" rx="5" fill="#60a5fa"/><text x="12" y="16" font-size="12" font-weight="700" text-anchor="middle" fill="#0f172a">A</text></svg>',
        "Billa": '<svg class="logo" viewBox="0 0 24 24" role="img" aria-label="Billa"><rect x="2" y="2" width="20" height="20" rx="5" fill="#cc1f2c"/><text x="12" y="16" font-size="12" font-weight="700" text-anchor="middle" fill="#0f172a">B</text></svg>',
    }
    today_iso = date.today().isoformat()
    timeline_days = [date.fromisoformat(today_iso) + timedelta(days=i) for i in range(14)]

    # Group consecutive days into week blocks split at Monday (week start), so
    # the gap between weeks lands only on the Sunday->Monday boundary — not at a
    # fixed midpoint. Used by both the header and every body row.
    def group_weeks(items):
        blocks, cur = [], []
        for i, html in items:
            if i > 0 and timeline_days[i].weekday() == 0:
                blocks.append(cur)
                cur = []
            cur.append(html)
        if cur:
            blocks.append(cur)
        return "".join(f'<div class="wk">{"".join(b)}</div>' for b in blocks)

    # Header timeline: a label per week block (its start date) plus the weekday
    # initials, both grouped into the same Monday-split blocks as the rows.
    _blocks = []
    _cur = []
    for _i, _day in enumerate(timeline_days):
        if _i > 0 and _day.weekday() == 0:
            _blocks.append(_cur)
            _cur = []
        _cur.append(_day)
    if _cur:
        _blocks.append(_cur)
    table_rows = []
    for product in sorted(products):
        entries = sorted(
            products[product],
            key=lambda x: (x["val"] is None, x["val"] or 0),
        )
        first = entries[0] if entries else {}
        pretty = display_name(product, first.get("raw_name", product))
        nutrition_entry = nutrition.get(product, {})
        nutrition_values = nutrition_entry.get("values", {})
        nutrition_html = ""
        if nutrition_entry.get("status") == "found":
            labels = ("Energy", "Protein", "Carbs", "Fiber", "Vitamin C")
            parts = []
            for label in labels:
                value = nutrition_values.get(label)
                if value:
                    parts.append(f"{label}: {value['value']} {value['unit']}")
            if parts:
                source = nutrition_entry.get("source", "")
                nutrition_html = (
                    f'<span class="nutrition" title="Per 100 g; source: {esc(source)}">'
                    f'Nutrition / 100 g: {esc(" · ".join(parts))}</span>'
                )
        elif nutrition_entry.get("status") == "not_found":
            nutrition_html = '<span class="nutrition dim">Nutrition data unavailable</span>'
        img_path = cache_image(first.get("product_id", product), first.get("image_url", ""))
        img_tag = (
            f'<img class="thumb" src="{img_path}" alt="{esc(pretty)}" loading="lazy">'
            if img_path else '<span class="dim">no img</span>'
        )
        # Combined price+store cells, one per store (kept aligned). The logo
        # lives inside the Price column now (store toggling is done via the
        # legend, so a separate Store column is redundant). Each store carries
        # its OWN discount date range; row-level data-start/data-end is the
        # union (min start, max end) for the date filter.
        pp_cells = []
        date_cells = []
        union_start, union_end = None, None
        for e in entries:
            logo = STORE_LOGO.get(e["store"], "")
            color = STORE_COLOR.get(e["store"], "#64748b")
            dr = e.get("date_range", "") or ""
            s, en = parse_range(dr)
            if s and (union_start is None or s < union_start):
                union_start = s
            if en and (union_end is None or en > union_end):
                union_end = en
            # Price cell is only meaningful when we have a parsed value.
            v = e["val"]
            if v is not None:
                pp_cells.append(
                    f'<div class="ppcell" data-store="{esc(e["store"])}">'
                    f'{logo}<span class="pprice">{v:.2f} / {e["unit"]}</span></div>'
                )
            # Date cell: render whenever the entry has a date range, independent
            # of whether the price parsed — a discount date is valid data on its own.
            if s and en:
                active_start = date.fromisoformat(s)
                active_end = date.fromisoformat(en)
                day_items = []
                for day_index, day in enumerate(timeline_days):
                    active = active_start <= day <= active_end
                    classes = "day"
                    if active:
                        classes += " active"
                    day_items.append((
                        day_index,
                        f'<span class="{classes}" data-date="{day.isoformat()}" title="{esc(e["store"])} · {fmt_cz(day.isoformat())}">{_DOW1_CZ[day.weekday()]}</span>',
                    ))
                day_html = group_weeks(day_items)
                date_cells.append(
                    f'<div class="dcell" data-store="{esc(e["store"])}" '
                    f'data-s="{esc(s or "")}" data-e="{esc(en or "")}" '
                    f'aria-label="{esc(e["store"])}: {esc(fmt_cz(dr)) or "no dates"}" '
                    f'style="--store-color:{color}"><div class="timeline">{day_html}</div></div>'
                )
        spark_html = sparkline(lowest_series.get(product, [])) or '<span class="dim">1 run</span>'
        table_rows.append(
            f"<tr data-start=\"{esc(union_start or '')}\" data-end=\"{esc(union_end or '')}\">"
            f'<td class="prod"><a class="product-link" href="products/{esc(product)}.html">'
            f'{img_tag}<span class="pname">{esc(pretty)}</span></a></td>'
            f'<td class="pricelist">{"".join(pp_cells)}</td>'
            f'<td class="drange">{"".join(date_cells)}</td>'
            f'<td class="spark"><div class="sparkline">{spark_html}</div></td>'
            f"</tr>"
        )

    last_run = last_run or "unknown"
    n_products = len(table_rows)
    n_stores = len(stores_seen)
    # Empty-state notice: render it *inside* the table body as a full-width row so
    # the column headers stay exactly consistent with the populated view.
    if not table_rows:
        if not HISTORY_CSV.exists():
            msg = "No history.csv — run the scraper first."
        elif not rows:
            msg = "history.csv is empty."
        else:
            msg = "No active discounts right now."
        empty_notice = (
            f'<tr><td colspan="4" class="dim" '
            f'style="text-align:center;padding:24px">'
            f'{esc(msg)}</td></tr>'
        )
    else:
        empty_notice = ""
    # has_history still reflects "do we have 2+ scrape days" for the trend hint.

    # Date bounds for the range slider. Far-left = today, far-right = latest
    # discount end date among the *shown* (active-window) entries, so the
    # slider never reaches into stale history. Row cells keep their real
    # per-store date_range; this only sets the slider's max span.
    all_ends = []
    for e in current_entries.values():
        s, en = parse_range(e.get("date_range", ""))
        for d in (s, en):
            if d:
                all_ends.append(d)
    date_min = today_iso
    date_max = max([today_iso] + all_ends) if all_ends else today_iso
    # Slider uses integer day offsets: 0 = today. The PICKER spans the full
    # timeline grid (14 days, same as the column it mirrors), so the thumbs travel
    # across every day square. The default selected window is today -> latest
    # discount end (days_span), which is just the initial handle positions.
    picker_span = len(timeline_days) - 1
    days_span = (date.fromisoformat(date_max) - date.fromisoformat(today_iso)).days
    # One segment per day step (0 .. days_span, inclusive). The strip is rendered
    # as a segmented toggle; the selected day(s) are highlighted via JS.
    seg_count = days_span + 1
    segments_html = "".join('<span class="seg"></span>' for _ in range(seg_count))

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grocery Prices</title>
<style>
* {{ box-sizing: border-box; }}
/* Theme tokens. Dark = Tokyo Night; light = Catppuccin Latte.
   "system" = no data-theme attr, so the media query below follows the OS. */
:root {{
  --bg: #1a1b26; --fg: #c0caf5; --muted: #565f89; --dim: #3b4261;
  --border: #292e42; --track: #3b4261; --surface: #24283b; --chip-off-bg: #1a1b26;
  --accent: #7aa2f7; --price: #bb9af7; --slider: #7dcfff; --th-bg: #24283b;
  --toggle-bg: #1a1b26; --thumb-border: #1a1b26;
  --logo-ink: #0f172a;  /* dark letter ink used inside the store-logo badges; reused
                           for the weekday letters so they match the logos in every theme */
}}
:root[data-theme="light"] {{
  --bg: #eff1f5; --fg: #4c4f69; --muted: #6c6f85; --dim: #9ca0b0;
  --border: #ccd0da; --track: #9ca0b0; --surface: #e6e9ef; --chip-off-bg: #eff1f5;
  --accent: #1e66f5; --price: #8839ef; --slider: #179299; --th-bg: #dce0e8;
  --toggle-bg: #eff1f5; --thumb-border: #eff1f5;
}}
@media (prefers-color-scheme: light) {{
  :root:not([data-theme="dark"]) {{
    --bg: #eff1f5; --fg: #4c4f69; --muted: #6c6f85; --dim: #9ca0b0;
    --border: #ccd0da; --track: #9ca0b0; --surface: #e6e9ef; --chip-off-bg: #eff1f5;
    --accent: #1e66f5; --price: #8839ef; --slider: #179299; --th-bg: #dce0e8;
    --toggle-bg: #eff1f5; --thumb-border: #eff1f5;
  }}
}}
html {{ overflow-y: scroll; }}
body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0;
  background: var(--bg); color: var(--fg); padding: 16px; }}
/* Center the whole page horizontally while keeping the table's fixed column
   widths: width:max-content sizes the wrapper to the table's natural width
   (all columns preserved), margin:0 auto centers it on wide screens, and
   max-width:100% lets it shrink (the table's own max-width:100% then
   compresses the date column) instead of overflowing on narrow screens. */
.page {{ width: max-content; max-width: 100%; margin: 0 auto; }}
h1 {{ font-size: 1.3rem; margin: 0 0 4px; }}
.meta {{ color: var(--muted); font-size: .85rem; margin-bottom: 14px; }}
table {{ width: max-content; max-width: 100%; border-collapse: collapse; font-size: .9rem; table-layout: fixed; }}
/* max-content keeps each column at its set width (no leftover space to
   balloon a column); max-width:100% prevents overflow on small screens. */
th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
  vertical-align: top; overflow: hidden; }}
th {{ color: var(--muted); font-weight: 600; cursor: pointer; user-select: none;
  position: sticky; top: 0; background: var(--th-bg); }}
/* Fixed column widths keep the header stable whether the body is full, empty,
   or showing a date with no matches — no reflow / left-shift. The product column
   is wide enough to show full Czech product names without truncation; the date
   column absorbs the remaining width. */
/* The date column is pinned (by JS, fitDateColumn) to the real timeline width +
   a few px of slack so there is no empty clickable strip to the right of the
   last day square. Without that, the wide column let clicks far right of the
   dates Voronoi-map onto the last day (wireBodyDrag). The product column (auto)
   absorbs the table's leftover width instead. */
th[data-k="prod"] {{ width: 220px; }}
th[data-k="pricelist"] {{ width: 120px; }}
th[data-k="drange"] {{ width: auto; }}
th[data-k="spark"] {{ width: 150px; }}
th:hover {{ color: var(--fg); }}
th[data-k="prod"], td.prod {{ width: 220px; min-width: 220px; max-width: 220px; }}
td.prod {{ font-weight: 600; vertical-align: middle; overflow: visible; }}
td.prod .product-link {{ color: inherit; text-decoration: none; display: block; }}
td.prod .product-link:hover .pname {{ color: var(--accent); text-decoration: underline; }}
td.prod .thumb {{ width: 40px; height: 40px; display: inline-block; vertical-align: middle;
  object-fit: contain; border-radius: 6px; background: var(--surface); margin-right: 6px; }}
td.prod .dim {{ display: inline-block; vertical-align: middle; }}
/* Long names wrap to as many lines as they need instead of being truncated.
   overflow-wrap:anywhere lets long single words (or concatenated Czech names)
   break so they never overflow the 220px column. */
td.prod .pname {{ display: inline-block; vertical-align: middle; max-width: calc(100% - 46px);
  line-height: 1.2; overflow-wrap: anywhere; word-break: break-word; }}
td.prod .nutrition {{ display: block; margin-top: 5px; font-size: .7rem; line-height: 1.35;
  font-weight: 400; color: var(--muted); white-space: normal; }}
td.pricelist {{ white-space: nowrap; vertical-align: middle; width: 1%; }}
.ppcell {{ display: flex; flex-direction: row-reverse; justify-content: flex-start;
  align-items: center; gap: 6px; padding: 1px 0; cursor: pointer; }}
.ppcell:hover {{ filter: brightness(1.15); }}
.ppcell .logo {{ width: 18px; height: 18px; flex: 0 0 auto; }}
.ppcell .pprice {{ font-variant-numeric: tabular-nums; font-weight: 700; white-space: nowrap;
  font-size: .95rem; }}
.drange {{ vertical-align: middle; width: auto; }}
.dcell {{ padding: 2px 0 2px 6px; }}
.timeline {{ display: flex; gap: 8px; align-items: center; width: max-content; }}
.timeline .wk {{ display: flex; gap: 3px; }}
.day {{ width: 13px; height: 13px; border-radius: 3px; background: var(--track); opacity: .28; cursor: pointer;
  display: flex; align-items: center; justify-content: center; font-size: 8px; line-height: 1; font-weight: 700; color: var(--logo-ink); }}
.day.active {{ background: var(--store-color); opacity: 1; }}
/* selected by the date-range / single-date picker (mirrors the picker's
   highlighted segments) */
.day.sel {{ outline: 2px solid var(--fg); outline-offset: 1px; }}
.timeline-head {{ display: flex; flex-direction: column; gap: 3px; }}
.timeline-head .weeks {{ display: flex; gap: 8px; }}
.timeline-head .wk {{ display: flex; gap: 3px; }}
.timeline-head .weeks {{ margin-bottom: 3px; color: var(--muted); font-size: .68rem; }}
.timeline-head .wklabel {{ text-align: center; }}
.spark {{ width: 130px; }}
.sparkline {{ transition: opacity .15s; }}
.sparkline.muted {{ opacity: .3; }}
.sparkline.datedim {{ opacity: .28; }}
.dim {{ color: var(--dim); font-size: .8rem; }}
/* muted = filtered-out store: dimmed for comparison, not removed */
.ppcell.muted {{ opacity: .3; transition: opacity .15s; }}
.dcell.muted {{ opacity: .3; transition: opacity .15s; }}
/* datedim = store has no discount inside the selected date window (but one is
   tracked within the two weeks) — fade its line out, keep the row for context */
.ppcell.datedim {{ opacity: .28; transition: opacity .15s; }}
.dcell.datedim {{ opacity: 1; transition: opacity .15s; }}
.dcell.datedim .day.active {{ opacity: .28; }}
/* rowout = the whole product is filtered out (every store muted, or its
   discount window is entirely outside the selected range). Fade the name AND
   the price block too, not just the date lines / individual store blocks. */
tr.rowout td.prod {{ opacity: .35; transition: opacity .15s; }}
tr.rowout td.prod .thumb {{ opacity: .35; }}
tr.rowout .ppcell {{ opacity: .35; }}
/* When a product is fully filtered out (every store line muted or out of the
   selected date range) under normal filtering, fade its name to match the
   .datedim/.muted level so the whole row dims uniformly. */
td.prod.prodfade {{ opacity: .28; transition: opacity .15s; }}
.legend {{ display: flex; gap: 12px; margin: 6px 0 14px; font-size: .82rem; }}
.legend .chip {{ display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  padding: 4px 10px; border-radius: 999px; border: 1px solid var(--track);
  background: var(--surface); user-select: none; transition: opacity .15s, background .15s; }}
.legend .chip .logo {{ width: 18px; height: 18px; }}
.legend .chip.off {{ opacity: .35; background: var(--chip-off-bg); border-color: var(--surface); }}
.legend .chip:hover {{ border-color: var(--dim); }}
.toggle {{ cursor: pointer; padding: 6px 14px; border-radius: 999px;
  border: 1px solid var(--track); background: var(--toggle-bg); color: var(--fg); font-size: .85rem;
  user-select: none; transition: background .15s, border-color .15s; }}
.toggle.on {{ background: var(--accent); color: var(--thumb-border); border-color: var(--accent); }}
.toggles {{ display: flex; align-items: center; gap: 8px; }}
.topbar {{ display: flex; align-items: center; justify-content: space-between; gap: 16px;
  margin: 0 0 10px; }}
.topbar h1 {{ margin: 0; font-size: 1.5rem; }}
.theme {{ display: inline-flex; border: 1px solid var(--track); border-radius: 999px; overflow: hidden; }}
.theme button {{ cursor: pointer; border: 0; background: var(--toggle-bg); color: var(--fg);
  font-size: .85rem; padding: 6px 12px; }}
.theme button + button {{ border-left: 1px solid var(--track); }}
.theme button.active {{ background: var(--accent); color: var(--thumb-border); font-weight: 600; }}
.timeline-head {{ display: flex; flex-direction: column; gap: 3px; }}
.date-label {{ color: var(--muted); font-weight: 600; font-size: .85rem; }}
</style></head>
<body>
<div class="page">
<div class="topbar">
  <h1>🪸 Grocery Prices</h1>
  <div class="theme" id="themeSwitch">
    <button data-theme="light">Light</button>
    <button data-theme="dark">Dark</button>
    <button data-theme="system">System</button>
  </div>
  <div class="toggles">
    <span class="toggle" id="hideToggle" title="Hide rows whose discounts fall outside the selected date range">Hide irrelevant</span>
  </div>
</div>
<div class="meta">Last run: {esc(last_run)} &middot; {n_products} products &middot;
 {n_stores} stores</div>
<div class="legend">
  <span class="chip" data-store="Lidl">{STORE_LOGO['Lidl']} Lidl</span>
  <span class="chip" data-store="Tesco">{STORE_LOGO['Tesco']} Tesco</span>
  <span class="chip" data-store="Albert">{STORE_LOGO['Albert']} Albert</span>
  <span class="chip" data-store="Billa">{STORE_LOGO['Billa']} Billa</span>
</div>
<table id="t">
<thead><tr>
<th data-k="prod">Product</th>
<th data-k="pricelist">Price</th>
<th data-k="drange" title="Discount days for the next two weeks"><div class="timeline-head">
  <span class="date-label" id="dateLabel">Date</span>
</div></th>
<th data-k="spark">Trend</th>
</tr></thead>
<tbody>
{''.join(table_rows)}
{empty_notice}</tbody></table>
</div>
<script>
const t = document.getElementById('t');
const tb = t.tBodies[0];
const rows = [...tb.rows];

// Hover tooltip: show the full product name only when the .pname is actually
// truncated (doesn't fit the column). A native title on every row would pop on
// hover pointlessly for names that already fit, so we set title dynamically.
function syncNameTitles() {{
  document.querySelectorAll('#t .pname').forEach(el => {{
    const truncated = el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight;
    if (truncated) {{
      el.title = el.textContent;
      el.style.cursor = 'help';
    }} else {{
      el.removeAttribute('title');
      el.style.cursor = '';
    }}
  }});
}}
syncNameTitles();
window.addEventListener('resize', syncNameTitles);

document.querySelectorAll('th').forEach(th => {{
  th.onclick = () => {{
    const k = th.dataset.k;
    const dir = th.dataset.d === '1' ? -1 : 1;
    th.dataset.d = dir;
    rows.sort((a,b) => {{
      let x = a.children[th.cellIndex].textContent.trim();
      let y = b.children[th.cellIndex].textContent.trim();
      return x.localeCompare(y)*dir;
    }});
    rows.forEach(r => tb.appendChild(r));
  }};
}});

// Combined filters: store mute + date-range / single-date slider.
const hidden = new Set();
let hideIrrelevant = false;  // when true, hide rows that don't match the date range (toggleable)
const chips = [...document.querySelectorAll('.legend .chip')];
// Today = the build-day anchor (server's date.today()). Slider works in
// integer day offsets from it. Compute via UTC date parts so the local
// timezone can't roll the day backwards (new Date("...T00:00:00").toISOString()
// would shift by the UTC offset).
const todayParts = "{today_iso}".split("-").map(Number);  // [Y, M, D]

function offsetToIso(off) {{
  const d = new Date(Date.UTC(todayParts[0], todayParts[1] - 1, todayParts[2] + off));
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${{y}}-${{m}}-${{day}}`;
}}
let rangeStart = offsetToIso(0);
let rangeEnd = offsetToIso({picker_span});

function inRange(row) {{
  const s = row.dataset.start || rangeStart;
  const e = row.dataset.end || rangeEnd;
  return s <= rangeEnd && e >= rangeStart;   // overlap test
}}
function applyFilters() {{
  // Mirror the picker selection onto the body timelines: only the UPPER and LOWER
  // bound days of the chosen window get a black-outline box (single-date mode ->
  // just that one day), so the frame marks the range edges, not every day.
  // Skip squares belonging to a muted (filtered-out) store, so a multi-store
  // product keeps the filter frame only on the lines that are still shown.
  document.querySelectorAll('#t .day').forEach(d => {{
    const ds = d.dataset.date;
    const owner = d.closest('.dcell');
    const ownerStore = owner && owner.dataset.store;
    const muted = !!(ownerStore && hidden.has(ownerStore));
    const on = !!ds && !muted && (ds === rangeStart || ds === rangeEnd);
    d.classList.toggle('sel', on);
  }});

  rows.forEach(r => {{
    // Pair each store line's price cell (.ppcell) and date cell (.dcell) so we
    // can show/hide them together — that keeps the Price column and the date
    // column aligned when individual lines drop out.
    const lines = [];
    const ppByStore = {{}};
    r.querySelectorAll('.ppcell').forEach(c => {{ ppByStore[c.dataset.store] = c; }});
    r.querySelectorAll('.dcell').forEach(c => {{
      const st = c.dataset.store;
      lines.push({{ store: st, pp: ppByStore[st] || null, dc: c }});
    }});
    let visibleStores = 0;
    let anyLineShown = false;
    let shown = 0;   // lines actually visible under current filters (not muted AND in window)
    lines.forEach(L => {{
      const st = L.store;
      const muted = !!(st && hidden.has(st));
      if (L.pp) L.pp.classList.toggle('muted', muted);
      L.dc.classList.toggle('muted', muted);
      if (!muted) visibleStores++;
      const ds = L.dc.dataset.s, de = L.dc.dataset.e;
      const hasDate = !!ds;
      const overlap = hasDate && ds <= rangeEnd && (de || ds) >= rangeStart;
      const inWin = hasDate ? overlap : true;   // a line with no date isn't date-filtered
      if (!muted && inWin) shown++;
      const dim = !muted && hasDate && !overlap;
      if (L.pp) L.pp.classList.toggle('datedim', dim);
      L.dc.classList.toggle('datedim', dim);
      const lineShown = !hideIrrelevant ? true : (!muted && overlap);
      if (lineShown) anyLineShown = true;
      const disp = lineShown ? '' : 'none';
      if (L.pp) L.pp.style.display = disp;
      L.dc.style.display = disp;
    }});

    // Fade the product name too when the whole product is filtered out — i.e.
    // every store line is muted or falls outside the selected date range. If a
    // product still has any visible line (another store, or in-window), its name
    // stays fully visible. Matches the .datedim/.muted fade level (.28).
    const prodCell = r.querySelector('td.prod');
    if (prodCell) prodCell.classList.toggle('prodfade', shown === 0);
    // Whole-row "fully out" fade (name + price) is opt-in: only when
    // "Hide irrelevant" is on. By default every row stays fully visible.
    const fullyOut = hideIrrelevant && ((visibleStores === 0) || !inRange(r));
    r.classList.toggle('rowout', fullyOut);
    let show = anyLineShown;
    if (!hideIrrelevant) show = true;                // default: keep all rows
    r.style.display = show ? '' : 'none';
  }});
}}
// Toggle a store's visibility (used by both the legend chips and the store
// icons in the Price column). Reflects state on the chip (.off) and on every
// .ppcell belonging to that store (.muted look) via applyFilters().
function setStoreHidden(s, hide) {{
  if (hide) {{ hidden.add(s); }} else {{ hidden.delete(s); }}
  const chip = chips.find(c => c.dataset.store === s);
  if (chip) chip.classList.toggle('off', hide);
  applyFilters();
}}
chips.forEach(chip => {{
  chip.onclick = () => {{
    const s = chip.dataset.store;
    setStoreHidden(s, !hidden.has(s));
  }};
}});
// Clicking a store icon in the Price column toggles that store's filter too.
document.querySelectorAll('#t .ppcell').forEach(pc => {{
  pc.style.cursor = 'pointer';
  pc.onclick = (e) => {{
    e.stopPropagation();
    const s = pc.dataset.store;
    setStoreHidden(s, !hidden.has(s));
  }};
}});

// The header date picker was removed — the body date-column squares are the
// only date control now. The header just shows a static "Date" column label.
let offS = 0, offE = {picker_span};   // selected day offsets (0 = today)
const SPAN_MAX = {picker_span};       // last day offset (full window)
function syncView() {{
  const ds = offsetToIso(offS), de = offsetToIso(offE);
  rangeStart = ds; rangeEnd = de;
  applyFilters();
}}
// Drag-to-paint range selection: press a square (start), drag across to another
// (end), release commits. Removes the "which edge moves?" ambiguity — you define
// both thresholds by where you start and stop. A plain click also works (start =
// end, so it collapses to a single day, then drag extends it).
function setRange(a, b) {{
  offS = Math.min(a, b); offE = Math.max(a, b);
  syncView();
}}
// Tap on a single day: if that day is already the ONLY selected one, reset the
// selection to the full window (all days); otherwise collapse to that single day.
function tapSelectDays(off) {{
  if (offS === offE && offS === off) setRange(0, SPAN_MAX);
  else setRange(off, off);
}}
// Wire drag-to-paint onto the range strip via pointer events (mouse + touch).
// The target square is derived purely from the pointer's X position relative to
// the strip, ignoring Y — so you can drag anywhere along the vertical axis (above
// or below the squares, in the gaps) and the correct day still fills in, as long
// as the X corresponds to that day's column.
syncView();

// Reverse of offsetToIso: an ISO date (YYYY-MM-DD) -> integer day offset from
// today (the build anchor). Used to map a body date-column square (which carries
// its real ISO date) back onto the same offset scale the picker uses.
function isoToOffset(iso) {{
  const p = iso.split("-").map(Number);
  const d = new Date(Date.UTC(p[0], p[1] - 1, p[2]));
  const t = new Date(Date.UTC(todayParts[0], todayParts[1] - 1, todayParts[2]));
  return Math.round((d - t) / 86400000);
}}
// The date-column squares share the SAME 14-day grid and X layout as the picker,
// so dragging/clicking them drives the very same selection (and thus filters all
// products) — just like the picker itself. Y is ignored; only the X (the day's
// column) decides which squares fill in.
(function wireBodyDrag() {{
  const body = document.getElementById('t').querySelector('tbody');
  let dragging = false, anchor = -1;
  const idxFromX = (x) => {{
    // Voronoi by square center: gaps between day squares map to the nearest day.
    const days = Array.from(body.querySelectorAll('.day'));
    let best = -1, bestD = Infinity;
    for (let i = 0; i < days.length; i++) {{
      const r = days[i].getBoundingClientRect();
      const c = r.left + r.width / 2;
      const d = Math.abs(x - c);
      if (d < bestD) {{ bestD = d; best = i; }}
    }}
    return best;
  }};
  const offAt = (i) => {{
    const sq = body.querySelectorAll('.day')[i];
    return sq ? isoToOffset(sq.dataset.date) : -1;
  }};
  const ondown = (e) => {{
    if (e.button !== 0) return;   // primary/left only; ignore middle (1) / right (2)
    // Whole date-column cell is the hit target (mirrors the old header picker):
    // gaps between squares AND the slack to the right of the last square all
    // Voronoi-map onto the nearest day, so nothing in the column is dead.
    const cell = e.target.closest('td.drange');
    if (!cell) return;
    e.preventDefault();
    const i = idxFromX(e.clientX);
    const off = offAt(i);
    if (off < 0) return;
    dragging = true; anchor = off;
    tapSelectDays(off);
    body.setPointerCapture && e.pointerId != null && body.setPointerCapture(e.pointerId);
  }};
  const onmove = (e) => {{
    if (!dragging) return;
    const i = idxFromX(e.clientX);
    const off = offAt(i);
    if (off < 0 || off === anchor) return;
    setRange(anchor, off);
  }};
  const onup = () => {{ dragging = false; anchor = -1; }};
  body.addEventListener('pointerdown', ondown);
  body.addEventListener('pointermove', onmove);
  body.addEventListener('pointerup', onup);
  body.addEventListener('pointercancel', onup);
}})();

// "Hide irrelevant" toggle: opt-in whole-row hiding for discounts that
// fall outside the selected date range. Default OFF — every row stays visible
// (dimmed where out of range) so the table never jumps.
const hideBtn = document.getElementById('hideToggle');
hideBtn.onclick = () => {{
  hideIrrelevant = !hideIrrelevant;
  hideBtn.classList.toggle('on', hideIrrelevant);
  applyFilters();
}};

// Pin the date column to the real timeline width (+ a few px slack) so there
// is no empty clickable strip to the right of the last day square. The body
// drag handler (wireBodyDrag) maps any X right of the timeline to the nearest
// day square by center, so a wide "auto" column let far-right clicks collapse
// the selection onto the last day. We measure the CONTENT span (first -> last
// day square), not the .timeline box, because .timeline is display:flex and
// stretches to fill the cell — its box width would be the (wrong) full column.
function fitDateColumn() {{
  const ref = document.querySelector('#t tbody .dcell .timeline');
  const th = document.querySelector('th[data-k="drange"]');
  if (!ref || !th) return;
  const days = ref.querySelectorAll('.day');
  let w = 0;
  if (days.length) {{
    const a = days[0].getBoundingClientRect();
    const b = days[days.length - 1].getBoundingClientRect();
    w = b.right - a.left;
  }}
  th.style.width = (Math.ceil(w) + 10) + 'px';
}}
fitDateColumn();
window.addEventListener('resize', fitDateColumn);

// Light / Dark / System theme switcher (persisted in localStorage).
const themeSwitch = document.getElementById('themeSwitch');
const THEME_KEY = 'grocery-theme';
function applyTheme(mode) {{
  if (mode === 'light' || mode === 'dark') {{
    document.documentElement.setAttribute('data-theme', mode);
  }} else {{
    document.documentElement.removeAttribute('data-theme'); // follow OS
  }}
  themeSwitch.querySelectorAll('button').forEach(b => {{
    b.classList.toggle('active', b.dataset.theme === mode);
  }});
}}
themeSwitch.querySelectorAll('button').forEach(b => {{
  b.onclick = () => {{
    const mode = b.dataset.theme;
    applyTheme(mode);
    try {{ localStorage.setItem(THEME_KEY, mode); }} catch (e) {{}}
  }};
}});
let saved = 'system';
try {{ saved = localStorage.getItem(THEME_KEY) || 'system'; }} catch (e) {{}}
applyTheme(saved);
</script>
</body></html>"""
    return html


def main() -> None:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    html = build()
    INDEX_HTML.write_text(html, encoding="utf-8")
    print(f"Wrote {INDEX_HTML.name} ({len(html)} bytes) into {SITE_DIR.name}/.")


if __name__ == "__main__":
    main()
