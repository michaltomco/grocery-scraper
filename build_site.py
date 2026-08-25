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
import json
import re
import shutil
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scrapers.common import (
    canonical_product_name,
    read_csv,
    ROOT,
    HISTORY_CSV,
    KUPI_FOOD_CATEGORIES,
    STORE_WORDS,
    number,
    parsed_date_range,
    is_true,
    is_active_offer,
    store_logo,
    store_color,
    store_initial,
)
from nutrition import get_many, get_many_exact

SITE_DIR = ROOT / "site"
IMG_DIR = SITE_DIR / "img"
PRODUCTS_DIR = SITE_DIR / "products"
PRODUCTS_EXACT_DIR = PRODUCTS_DIR / "exact"
INDEX_HTML = SITE_DIR / "index.html"
KUPI_PRICE_HISTORY_CSV = ROOT / "kupi_price_history.csv"

# Download each product image exactly once, keyed by product_id. Already-cached
# files are never re-fetched. Each image is normalized onto a square white
# canvas (largest-side scaled to 220px, centered, no cropping) so every
# thumbnail shares one aspect ratio and the white padding blends with the
# product's own white background. Returns a web path relative to the site root.
THUMB = 220  # px, size of the normalized square canvas
FALLBACK_IMAGE = IMG_DIR / "veg.png"


def fallback_image() -> str:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    if not FALLBACK_IMAGE.exists():
        shutil.copyfile(ROOT / "veg.png", FALLBACK_IMAGE)
    return "img/veg.png"


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
    if not image_url or any(marker in image_url.lower() for marker in ("no-image", "no_image", "/no_img/", "placeholder")):
        return fallback_image()
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
        return fallback_image()
    finally:
        if raw.exists():
            try:
                raw.unlink()
            except OSError:
                pass
    return f"img/{dest.name}"


def normalize_kupi_graph_price(price: str, unit: str) -> tuple[float | None, str]:
    """Normalize a Kupi graph price to the dashboard's kg/piece units."""
    value = number(price)
    normalized_unit = " ".join(str(unit).lower().split())
    if value is None:
        return None, ""
    if normalized_unit == "1 kg":
        return value, "kg"
    if normalized_unit == "100 g":
        return value * 10, "kg"
    if normalized_unit in {"1 ks", "1 pc", "1 piece"}:
        return value, "ks"
    return None, ""


def kupi_graph_lowest_series(
    path: Path = KUPI_PRICE_HISTORY_CSV,
    today: date | None = None,
) -> dict[tuple[str, str], dict[date, float]]:
    """Load Kupi's historical lowest-discount graph as normalized daily prices.

    Future graph points are excluded because the dashboard's offer timeline owns
    future pricing; graph rows are only historical context for the sparkline.
    """
    today = today or date.today()
    output: dict[tuple[str, str], dict[date, float]] = defaultdict(dict)
    if not path.exists():
        return output
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("series") != "lowest_discount":
                continue
            try:
                observed = date.fromisoformat(row.get("observed_date", ""))
            except ValueError:
                continue
            if observed > today:
                continue
            value, unit = normalize_kupi_graph_price(row.get("price", ""), row.get("unit", ""))
            if value is None:
                continue
            key = (row.get("canonical_product_name", ""), unit)
            previous = output[key].get(observed)
            if previous is None or value < previous:
                output[key][observed] = value
    return output


def merge_daily_trends(
    graph_days: dict[date, float],
    local_days: dict[date, float],
) -> dict[date, float]:
    """Overlay exact local observations onto Kupi's aggregate historical graph."""
    merged = dict(graph_days)
    merged.update(local_days)
    return merged


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


def explicit_weight_grams(name: str) -> float | None:
    """Return package weight when it is explicitly present in the name."""
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(kg|g)\b", name or "", re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1).replace(",", "."))
    return value * 1000 if match.group(2).lower() == "kg" else value


# Conservative edible-weight estimates for unmistakable whole produce sold per
# piece. These are deliberately kept separate from explicit package weights and
# shown as estimates in the nutrient ranking.
PIECE_WEIGHT_ESTIMATES_G = {
    "ananas": 900, "avokado": 150, "brokolice": 500, "celer_rapikaty": 400,
    "cesnek": 10, "fiky_cerstve": 50, "jarni_cibule_svazek": 100,
    "kedlubna": 300, "kiwi": 75, "kopr_cerstvy_svazek": 50,
    "kukurice_klas": 200, "kukurice_klas_ceska_farma": 200,
    "kukurice_predvarena": 150, "kvetak": 800, "limety": 70, "mango": 300,
    "mrkev": 100, "okurka": 400, "paprika_cervena": 150, "porek": 250,
    "salat_little_gem": 150, "salat_little_gem_bio_nature_s_promise": 150,
}


def estimated_piece_weight_grams(product: str) -> float | None:
    return PIECE_WEIGHT_ESTIMATES_G.get(product)


def rda_amount_in_unit(label: str, unit: str) -> float | None:
    target = RDA_VALUES.get(label)
    if not target:
        return None
    amount, target_unit = target
    unit = str(unit).lower().replace("μ", "µ")
    if unit in {"ug", "mcg"}:
        unit = "µg"
    target_unit = str(target_unit).lower().replace("μ", "µ")
    if label == "Vitamin A" and unit in {"iu", "i.u."}:
        return None
    if unit == target_unit:
        return amount
    if unit in {"ug", "µg", "mcg"} and target_unit == "mg":
        return amount * 1000
    if unit == "mg" and target_unit == "µg":
        return amount / 1000
    if unit == "g" and target_unit == "mg":
        return amount / 1000
    if unit == "mg" and target_unit == "g":
        return amount * 1000
    return None


def parse_ts(value: str) -> datetime:
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
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
    "paprika_cervena": "Paprika červená",
    "rajcata": "Rajčata",
    "okurka": "Okurka",
    "jablka": "Jablka",
    "banany": "Banány",
    "hrozny": "Hrozny",
    "jarni_cibule_svazek": "Jarní cibule",
}

RDA_VALUES = {
    "Calories": (2000, "kcal"), "Protein": (160, "g"), "Fat": (100, "g"),
    "Carbs": (135, "g"), "Fiber": (40, "g"),
    "Omega-3 fat": (1.6, "g"), "Omega-6 fat": (17, "g"),
    "Vitamin A": (900, "µg"), "Vitamin B1": (1.2, "mg"), "Vitamin B2": (1.3, "mg"),
    "Vitamin B3": (16, "mg"), "Vitamin B5": (5, "mg"), "Vitamin B6": (1.3, "mg"),
    "Vitamin B7 (Biotin)": (30, "µg"), "Vitamin B9": (400, "µg"),
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
    if label == "Vitamin A" and normalized_unit in {"iu", "i.u."}:
        return None
    if normalized_unit in {"ug", "µg", "mcg"} and target_unit == "mg":
        amount /= 1000
    elif normalized_unit == "g" and target_unit == "mg":
        amount *= 1000
    elif normalized_unit in {"mg", "g"} and target_unit == "µg":
        amount = amount * (1_000_000 if normalized_unit == "g" else 1000)
    return round(amount / RDA_VALUES[label][0] * 100)


def display_name(product: str, raw_name: str) -> str:
    return GENERAL_DISPLAY_NAMES.get(product, pretty_name(raw_name))


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



# CSS and JS extracted from the canonical product page template so the
# exact variant pages share the identical styling/behaviour (no drift).
PRODUCT_PAGE_CSS = '\n:root { --bg:#1a1b26; --fg:#c0caf5; --muted:#565f89; --surface:#24283b; --border:#292e42; --accent:#7aa2f7; --track:#3b4261; --axis-track:#3b4261; --logo-ink:#0f172a; }\n:root[data-theme="light"] { --bg:#eff1f5; --fg:#4c4f69; --muted:#4c4f69; --surface:#e6e9ef; --border:#ccd0da; --accent:#1e66f5; --track:#9ca0b0; --axis-track:#a3a8b8; --logo-ink:#0f172a; }\n@media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) { --bg:#eff1f5; --fg:#4c4f69; --muted:#4c4f69; --surface:#e6e9ef; --border:#ccd0da; --accent:#1e66f5; --track:#9ca0b0; --axis-track:#a3a8b8; } }\nbody { font-family: -apple-system, system-ui, sans-serif; max-width: 900px; margin: 0 auto;\n  padding: 24px; background: var(--bg); color: var(--fg); }\na { color: var(--accent); } .muted { color: var(--muted); }\n.hero { width: 180px; height: 180px; object-fit: contain; background: var(--surface); border-radius: 12px; }\n.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin: 16px 0; }\n.nutrition-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }\n.nutrition-group { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }\n.nutrition-group h2 { color: var(--accent); }\n.product-summary { display:grid; grid-template-columns:minmax(180px, 1fr) minmax(360px, 2fr); gap:16px; align-items:start; }\n.product-visual { min-width:0; }\n.product-summary > .card { margin-top:0; }\n.store-chip { display:inline-flex; align-items:center; gap:6px; font-weight:600; cursor:pointer; }\n.discount-price { font-weight:700; cursor:pointer; }\n.discount-row-off td { opacity:.3; }\n.muted td { opacity:.3; color:var(--muted); }\n.datedim td { opacity:1; color:var(--muted); }\n.muted .store-chip { opacity:1; color:var(--muted); }\n.datedim .store-chip, .datedim .discount-price { opacity:.28; color:var(--muted); }\n.muted .store-mark, .datedim .store-mark { opacity:.35; }\n.datedim .day.active { opacity:.28; }\n.store-chip.off { opacity:.35; }\n.muted .store-chip.off { opacity:1; }\n.store-mark { display:inline-flex; align-items:center; justify-content:center; width:22px; height:22px; border-radius:6px; background:var(--store-color); color:#0f172a; font-weight:700; font-size:.8rem; }\n.nutrition-heading { display:flex; justify-content:space-between; align-items:center; gap:12px; }\n.nutrition-mode { display:flex; border:1px solid var(--border); border-radius:999px; overflow:hidden; }\n.nutrition-mode button { border:0; padding:5px 9px; background:transparent; color:var(--fg); cursor:pointer; }\n.nutrition-mode button + button { border-left:1px solid var(--border); }\n.nutrition-mode button.active { background:var(--accent); color:var(--bg); font-weight:600; }\n.produce-nav { display:flex; gap:8px; position:relative; top:6px; }\n.title-row { display:flex; align-items:center; justify-content:space-between; gap:16px; }\n.nav-button { display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--border); border-radius:999px; padding:3px 8px; font-size:14px; line-height:1.2; font-weight:700; text-decoration:none; color:var(--fg); background:var(--surface); }\n.nav-button:hover { border-color:var(--accent); color:var(--accent); }\n.back-button { display:inline-block; border:1px solid var(--border); border-radius:999px; padding:6px 12px; text-decoration:none; color:var(--fg); background:var(--surface); }\n.back-button:hover { border-color:var(--accent); color:var(--accent); }\n.mode-rda, .mode-axis { display:none; }\n.mode-axis { width:135px; align-items:center; justify-content:flex-end; gap:5px; }\n.axis { display:inline-block; vertical-align:middle; width:100px; height:10px; margin-right:5px; background:var(--axis-track); border:0; border-radius:4px; overflow:hidden; }\n.axis-fill { display:block; min-width:3px; height:100%; background:var(--accent); border-radius:3px; }\n.axis-label { font-size:.8em; font-weight:600; color:var(--fg); }\n.timeline { display:flex; gap:8px; align-items:center; width:max-content; }\n.timeline .wk { display:flex; gap:3px; }\n.day { width:13px; height:13px; border-radius:3px; background:var(--track); opacity:.28;\n  display:flex; align-items:center; justify-content:center; font-size:8px; line-height:1;\n  font-weight:700; color:var(--logo-ink); cursor:pointer; user-select:none; }\n.day.active { background:var(--store-color, var(--accent)); opacity:1; color:#0f172a; border:0; }\n.day.sel { outline:2px solid var(--fg); outline-offset:1px; }\n.topbar { display:flex; justify-content:space-between; align-items:center; gap:16px; }\n.theme { display:flex; border:1px solid var(--border); border-radius:999px; overflow:hidden; }\n.theme button { border:0; padding:6px 10px; background:transparent; color:var(--fg); cursor:pointer; }\n.theme button + button { border-left:1px solid var(--border); }\n.theme button.active { background:var(--accent); color:var(--bg); font-weight:600; }\n@media (max-width: 700px) { .nutrition-grid, .product-summary { grid-template-columns: 1fr; } }\n@media (max-width: 700px) {\n  body { padding:12px; }\n  .topbar { flex-wrap:wrap; align-items:flex-start; gap:10px; }\n  .topbar h1 { flex:1 1 100%; font-size:1.25rem; }\n  .theme, .toggles { flex-wrap:wrap; }\n  .toggles { margin-left:0; }\n  .legend { flex-wrap:wrap; gap:6px; }\n  .card { overflow-x:auto; }\n  #t, #rankingTable { min-width:760px; }\n  .ranking-heading { flex-wrap:wrap; }\n  .ranking-heading > div { display:flex; flex-wrap:wrap; gap:6px; width:100%; }\n  .ranking-heading select { flex:1 1 140px; min-width:0; }\n}\ntable { border-collapse: collapse; width: 100%; } th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); }\n.nutrition-group th { text-align:left; width:50%; }\n.nutrition-group td { text-align:right; white-space:nowrap; }\nh1 { margin-bottom: 6px; } h2 { margin-top: 0; font-size: 1rem; }\n'
PRODUCT_PAGE_HEAD_JS = "<script>try { const t=localStorage.getItem('grocery-theme'); if (t==='light' || t==='dark') { document.documentElement.setAttribute('data-theme', t); document.documentElement.style.colorScheme=t; document.documentElement.style.backgroundColor=t==='dark'?'#1a1b26':'#eff1f5'; } } catch (e) {}</script>"
PRODUCT_PAGE_FILTER_JS = '<script>\nconst key = \'grocery-theme\';\nfunction applyTheme(mode) {\n  if (mode === \'light\' || mode === \'dark\') document.documentElement.setAttribute(\'data-theme\', mode);\n  else document.documentElement.removeAttribute(\'data-theme\');\n}\napplyTheme(localStorage.getItem(key) || \'system\');\nconst nutritionMode = document.getElementById(\'nutritionMode\');\nnutritionMode.querySelectorAll(\'button\').forEach(button => button.onclick = () => {\n  const mode = button.dataset.mode;\n  document.querySelectorAll(\'.mode-raw, .mode-rda, .mode-axis\').forEach(item => item.style.display = \'none\');\n  document.querySelectorAll(\'.mode-\' + mode).forEach(item => item.style.display = mode === \'axis\' ? \'flex\' : \'inline\');\n  nutritionMode.querySelectorAll(\'button\').forEach(item => item.classList.toggle(\'active\', item === button));\n  localStorage.setItem(\'grocery-nutrition-mode\', mode);\n});\nnutritionMode.querySelector(\'[data-mode="\' + (localStorage.getItem(\'grocery-nutrition-mode\') || \'axis\') + \'"]\').click();\nconst hidden = new Set();\nconst todayParts = "{date.today().isoformat()}".split("-").map(Number);\nfunction offsetToIso(off) {\n  const d = new Date(Date.UTC(todayParts[0], todayParts[1] - 1, todayParts[2] + off));\n  return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,\'0\')}-${String(d.getUTCDate()).padStart(2,\'0\')}`;\n}\nlet rangeStart = offsetToIso(0), rangeEnd = offsetToIso(13);\nfunction applyFilters() {\n  document.querySelectorAll(\'#discountTable .day\').forEach(day => day.classList.toggle(\'sel\', day.dataset.date === rangeStart || day.dataset.date === rangeEnd));\n  document.querySelectorAll(\'#discountTable tr[data-store]\').forEach(row => {\n    const storeMuted = hidden.has(row.dataset.store);\n    const outside = row.dataset.start && row.dataset.end && (row.dataset.end < rangeStart || row.dataset.start > rangeEnd);\n    row.classList.toggle(\'muted\', storeMuted);\n    row.classList.toggle(\'datedim\', !storeMuted && outside);\n  });\n}\nfunction setStoreHidden(store, hide) {\n  if (hide) hidden.add(store); else hidden.delete(store);\n  document.querySelectorAll(\'.store-chip\').forEach(chip => chip.classList.toggle(\'off\', hidden.has(chip.closest(\'tr\').dataset.store)));\n  applyFilters();\n}\ndocument.querySelectorAll(\'.store-chip\').forEach(chip => chip.onclick = () => setStoreHidden(chip.closest(\'tr\').dataset.store, !hidden.has(chip.closest(\'tr\').dataset.store)));\ndocument.querySelectorAll(\'.discount-price\').forEach(price => price.onclick = () => {\n  const store = price.closest(\'tr\').dataset.store;\n  setStoreHidden(store, !hidden.has(store));\n});\nlet selecting = false, anchor = null;\ndocument.querySelectorAll(\'#discountTable .day\').forEach(day => {\n  day.addEventListener(\'pointerdown\', event => {\n    event.preventDefault();\n    if (rangeStart === rangeEnd && day.dataset.date === rangeStart) {\n      selecting = false; rangeStart = offsetToIso(0); rangeEnd = offsetToIso(13); applyFilters(); return;\n    }\n    selecting = true; anchor = day.dataset.date; rangeStart = anchor; rangeEnd = anchor; applyFilters();\n  });\n  day.addEventListener(\'pointerenter\', () => { if (!selecting) return; rangeStart = anchor < day.dataset.date ? anchor : day.dataset.date; rangeEnd = anchor < day.dataset.date ? day.dataset.date : anchor; applyFilters(); });\n});\ndocument.addEventListener(\'pointerup\', () => { selecting = false; });\napplyFilters();\n</script>'

def slugify(text):
    """Stable slug for an exact product label, used in exact/ URLs."""
    out = []
    for ch in (text or "").lower():
        if ch.isalnum() or ch == "_":
            out.append(ch)
        elif ch.isspace():
            out.append("_")
    slug = "".join(out).strip("_")
    return slug or "product"


def exact_link(label, raw_name):
    """Render a raw_name as a link to its exact-SKU product page."""
    href = f"exact/{slugify(raw_name)}.html"
    return f'<a class="exact-link" href="{esc(href)}" aria-label="{label} nutrition">{label}</a>'


NUTRI_GROUPS = {
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


def nutrition_section(title, labels, values):
    vitamin_order = {
        "Vitamin A": 1, "Vitamin B1": 2, "Vitamin B2": 3, "Vitamin B3": 4,
        "Vitamin B5": 5, "Vitamin B6": 6, "Vitamin B7 (Biotin)": 7,
        "Vitamin B9": 8, "Vitamin B9 (Folate)": 8, "Vitamin B12": 9, "Vitamin C": 10,
        "Vitamin D": 11, "Vitamin E": 12, "Vitamin K": 13,
    }
    macro_order = {
        "Calories": 1, "Protein": 2, "Fat": 3, "Carbs": 4,
        "Omega-3 fat": 5, "Omega-6 fat": 6, "Fiber": 7,
    }
    ordered_values = sorted(
        ((label, value) for label, value in values.items() if label in labels),
        key=lambda item: (macro_order.get(item[0], 99) if title == "Calories & macros" else vitamin_order.get(item[0], item[0])),
    )
    omega_values = [
        float(value.get("value")) for label, value in ordered_values
        if label.startswith("Omega-") and isinstance(value, dict)
        and isinstance(value.get("value"), (int, float)) and value.get("value") > 0
    ]
    omega_axis_max = max(omega_values, default=0.0)
    if title == "Calories & macros" and values:
        present = {label for label, _ in ordered_values}
        ordered_values.extend(
            (label, {"value": 0, "unit": "g"})
            for label in ("Omega-3 fat", "Omega-6 fat")
            if label not in present
        )

    def value_html(label, value):
        amount = value.get("value")
        if amount == 0:
            return ('<span class="nutrient-value">'
                    '<span class="mode-raw">–</span><span class="mode-rda">–</span>'
                    '<span class="mode-axis"><span class="axis"><span class="axis-fill" style="width:0;min-width:0"></span></span></span>'
                    '</span>')
        unit = esc(value.get("unit", ""))
        percentage = rda_percent(label, amount, value.get("unit", ""))
        raw = f"{esc(amount)} {unit}"
        if percentage is None:
            if label.startswith("Omega-") and omega_axis_max > 0:
                width = min(max(float(amount) / omega_axis_max * 100, 0), 100)
                return ('<span class="nutrient-value">'
                        f'<span class="mode-raw">{raw}</span><span class="mode-rda">–</span>'
                        f'<span class="mode-axis"><span class="axis"><span class="axis-fill" style="width:{width:.2f}%"></span></span></span>'
                        f'</span>')
            return raw
        width = min(max(percentage, 0), 100)
        rda_display = '-' if percentage == 0 else f'{percentage}%'
        return ('<span class="nutrient-value">'
                f'<span class="mode-raw">{raw}</span>'
                f'<span class="mode-rda">{rda_display}</span>'
                f'<span class="mode-axis"><span class="axis"><span class="axis-fill" style="width:{width}%"></span></span></span>')

    rows = "".join(
        f'<tr><th>{esc((label.replace("Vitamin B9 (Folate)", "Vitamin B9").replace("Vitamin ", "").split(" (")[0]) if title == "Vitamins" else label.removesuffix(" fat"))}</th><td>{value_html(label, value)}</td></tr>'
        for label, value in ordered_values
    )
    if not rows:
        rows = '<tr><td colspan="2" class="muted">No data available</td></tr>'
    return f'<section class="nutrition-group"><h2>{title}</h2><table>{rows}</table></section>'


def nutrition_sections_for(values):
    return "".join(nutrition_section(t, labs, values) for t, labs in NUTRI_GROUPS.items())


def source_html_for(nutrition_entry, with_unavailable=True):
    source = nutrition_entry.get("source", "")
    source_product = nutrition_entry.get("source_product", "")
    source_url = nutrition_entry.get("source_url", "")
    provenance = nutrition_entry.get("provenance", "")
    if source:
        provenance_label = ""
        if provenance == "exact_match":
            provenance_label = " · exact entry"
        elif provenance == "category_proxy":
            provenance_label = " · category average"
        if source_url:
            source_detail = (
                f' — <a href="{esc(source_url)}" target="_blank" rel="noopener">'
                f'{esc(source_product)}</a>{provenance_label}'
            )
        else:
            source_detail = f' — {esc(source_product)}{provenance_label}'
        return f'<p class="muted">Source: {esc(source)}{source_detail}</p>'
    if with_unavailable:
        return '<p class="muted">Nutrition data unavailable — no exact manufacturer label found.</p>'
    return ""


def store_chip(store):
    color = store_color(store)
    initial = store_initial(store)
    return f'<span class="store-chip" style="--store-color:{color}"><span class="store-mark">{esc(initial)}</span>{esc(store)}</span>'


def detail_timeline(date_range, store, today_iso):
    timeline_days = [date.fromisoformat(today_iso) + timedelta(days=i) for i in range(14)]
    day_letters = ["P", "Ú", "S", "Č", "P", "S", "N"]
    start, end = parsed_date_range(date_range)
    start_date, end_date = _iso(start), _iso(end)
    if not start_date or not end_date:
        return '<span class="muted">No dates</span>'
    cells = []
    for day in timeline_days:
        active = start_date <= day <= end_date
        cells.append((
            timeline_days.index(day),
            f'<span class="day{" active" if active else ""}" data-date="{day.isoformat()}" '
            f'title="{esc(fmt_cz(day.isoformat()))}">{day_letters[day.weekday()]}</span>',
        ))
    color = store_color(store)
    weeks, current = [], []
    for index, cell in cells:
        if index > 0 and timeline_days[index].weekday() == 0:
            weeks.append(f'<div class="wk">{" ".join(current)}</div>')
            current = []
        current.append(cell)
    if current:
        weeks.append(f'<div class="wk">{" ".join(current)}</div>')
    return f'<div class="timeline" style="--store-color:{color}">' + "".join(weeks) + '</div>'


def render_product_page(pretty, image_html, nutrition_sections, source_html,
                        discount_rows, previous_html, next_html, today_iso,
                        back_href="../index.html", table_head="Name", table_title="Current discounts"):
    head_js_inline = (
        "try { const t=localStorage.getItem('grocery-theme'); "
        "if (t==='light' || t==='dark') { document.documentElement.setAttribute('data-theme', t); "
        "document.documentElement.style.colorScheme=t; "
        "document.documentElement.style.backgroundColor=t==='dark'?'#1a1b26':'#eff1f5'; } } catch (e) {}"
    )
    filter_js = PRODUCT_PAGE_FILTER_JS if table_title == "Current discounts" else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(pretty)} · Grocery Prices</title>
<script>{head_js_inline}</script>
<style>{PRODUCT_PAGE_CSS}</style></head><body>
<p><a class="back-button" href="{esc(back_href)}">Back</a></p>
<div class="title-row"><h1>{esc(pretty)}</h1><div class="produce-nav">{previous_html}{next_html}</div></div>
<div class="product-summary"><div class="product-visual">{image_html}</div><div class="card"><h2>{esc(table_title)}</h2><table id="discountTable"><thead><tr><th>{esc(table_head)}</th><th>Store</th><th>Price</th><th>Discount days</th></tr></thead><tbody>{discount_rows}</tbody></table></div></div>
<div class="card"><div class="nutrition-heading"><h2>Nutrition per 100 g</h2><div class="nutrition-mode" id="nutritionMode"><button data-mode="axis">Axis</button><button data-mode="rda">RDA</button><button data-mode="raw">Raw</button></div></div><div class="nutrition-grid">{nutrition_sections}</div>{source_html}</div>
</body>{PRODUCT_PAGE_HEAD_JS}{filter_js}</html>"""


def write_product_pages(products, nutrition):
    """Write one static detail page per canonical produce item."""
    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
    product_keys = sorted(products)
    today_iso = date.today().isoformat()
    for product_index, product in enumerate(product_keys):
        entries = products[product]
        first = entries[0] if entries else {}
        pretty = display_name(product, first.get("raw_name", product))
        img_path = cache_image(first.get("product_id", product), first.get("image_url", ""))
        image_html = f'<img class="hero" src="../{esc(img_path)}" alt="{esc(pretty)}">' if img_path else ""
        source_image = str(first.get("image_url", "")).lower()
        if not source_image or any(m in source_image for m in ("no-image", "no_image", "/no_img/", "placeholder")):
            nutrition_entry = {}
        else:
            nutrition_entry = nutrition.get(product, {})
        values = nutrition_entry.get("values", {})
        nutrition_sections = nutrition_sections_for(values)
        source_html = source_html_for(nutrition_entry, with_unavailable=False)
        discount_rows = "".join(
            f'<tr data-store="{esc(e.get("store", ""))}" data-start="{esc(parsed_date_range(e.get("date_range", ""))[0])}" data-end="{esc(parsed_date_range(e.get("date_range", ""))[1])}">'
            f'<td class="discount-produce">{exact_link(esc(e.get("raw_name", product)), e.get("raw_name", product))}</td>'
            f'<td>{store_chip(e.get("store", ""))}</td>'
            f'<td class="discount-price">{esc(e.get("val") if e.get("val") is not None else "-")} / {esc(e.get("unit", ""))}</td>'
            f'<td>{detail_timeline(e.get("date_range", ""), e.get("store", ""), today_iso)}</td></tr>'
            for e in sorted(entries, key=lambda x: (x.get("val") is None, x.get("val") if x.get("val") is not None else float("inf"), x.get("store", "")))
        )
        previous_html, next_html = "", ""
        if product_index > 0:
            previous = product_keys[product_index - 1]
            previous_html = f'<a class="nav-button" href="{previous}.html" aria-label="Previous produce" title="Previous produce">&lt;</a>'
        if product_index + 1 < len(product_keys):
            following = product_keys[product_index + 1]
            next_html = f'<a class="nav-button" href="{following}.html" aria-label="Next produce" title="Next produce">&gt;</a>'
        page = render_product_page(pretty, image_html, nutrition_sections, source_html, discount_rows, previous_html, next_html, today_iso)
        (PRODUCTS_DIR / f"{product}.html").write_text(page, encoding="utf-8")


def write_exact_product_pages(exact_nutrition, exact_rows):
    """Write one detail page per exact retailer SKU label.

    exact_rows maps slug -> info dict: raw_name, pretty, canonical_product_name,
    product_id, image_url, store, val, unit, date_range. Each page binds
    nutrition to the exact brand/variant and always shows its source. A SKU
    with no exact match renders the provenance-correct unavailable state.
    """
    PRODUCTS_EXACT_DIR.mkdir(parents=True, exist_ok=True)
    today_iso = date.today().isoformat()
    for slug, info in sorted(exact_rows.items()):
        raw_name = info["raw_name"]
        pretty = info["pretty"]
        img_path = cache_image(info.get("product_id", raw_name), info.get("image_url", ""))
        image_html = f'<img class="hero" src="../../{esc(img_path)}">' if img_path else ""
        source_image = str(info.get("image_url", "")).lower()
        if not source_image or any(m in source_image for m in ("no-image", "no_image", "/no_img/", "placeholder")):
            nutrition_entry = {}
        else:
            nutrition_entry = exact_nutrition.get(slug, {})
        values = nutrition_entry.get("values", {})
        nutrition_sections = nutrition_sections_for(values)
        source_html = source_html_for(nutrition_entry, with_unavailable=True)
        discount_rows = "".join(
            f'<tr data-store="{esc(info.get("store", ""))}" data-start="{esc(parsed_date_range(info.get("date_range", ""))[0])}" data-end="{esc(parsed_date_range(info.get("date_range", ""))[1])}">'
            f'<td>{esc(raw_name)}</td><td>{store_chip(info.get("store", ""))}</td>'
            f'<td class="discount-price">{info.get("val")} / {esc(info.get("unit", ""))}</td>'
            f'<td>{detail_timeline(info.get("date_range", ""), info.get("store", ""), today_iso)}</td></tr>'
        )
        canonical_name = info.get("canonical_product_name", slug)
        page = render_product_page(pretty, image_html, nutrition_sections, source_html, discount_rows, "", "", today_iso, back_href=f"../{canonical_name}.html", table_title=pretty + " · exact offer")
        (PRODUCTS_EXACT_DIR / f"{slug}.html").write_text(page, encoding="utf-8")

def build() -> str:
    if not HISTORY_CSV.exists():
        rows = []
    else:
        # cache_image normalizes new downloads before saving them. Avoid
        # re-encoding the tracked cache on every build: repeated lossy writes
        # create noisy image diffs without improving the deployed asset.
        rows = read_csv(HISTORY_CSV)

    # Nutrition enrichment covers fresh produce (USDA/Open Food Facts) and the
    # hand-curated NutriData.cz records for other categories. Curated records are
    # loaded inside get_many and take priority, so we request nutrition for every
    # canonical product regardless of category.
    nutrition = get_many(
        {
            canonical_product_name(r.get("product_name", ""))
            for r in rows
        },
        fetch_missing=False,
    )
    # Strict exact-label nutrition: one lookup per distinct retailer label.
    exact_labels = {r.get("product_name", "") for r in rows}
    exact_nutrition = get_many_exact(exact_labels, fetch_missing=False)

    today_iso = date.today().isoformat()
    today_date = date.fromisoformat(today_iso)
    # A discount is still relevant if its end date is today or later. We also
    # tolerate flyers whose start is a few days in the future (Kupi publishes
    # "st 5. 8. – út 11. 8." offers several days early) so those count as
    # current too, but anything that ended long ago is dropped as stale.
    # The exact rule lives in scrapers.common.is_active_offer.

    all_scrape_dates = sorted(
        {r.get("scraped_at", "")[:10] for r in rows if r.get("scraped_at")},
        reverse=True,
    )
    last_run = all_scrape_dates[0] if all_scrape_dates else ""
    has_history = len(all_scrape_dates) > 1

    # ACTIVE-WINDOW model: show every distinct discount that is valid today or
    # starts within the next two weeks. History has one copy per scrape day, so
    # deduplicate only identical offer identities—not by (product, store)—or an
    # overlapping current/next-week offer from the same store disappears.
    current_entries: dict[tuple[str, ...], dict] = {}
    series: dict[tuple[str, str, str], list[tuple[datetime, float]]] = defaultdict(list)
    stores_seen: set[str] = set()
    for r in rows:
        store = r.get("store", "")
        category = r.get("category") or "Ovoce a zelenina"
        # Re-canonicalize from the raw product name so that any recent
        # canonicalizer enhancements (e.g. avocado subcategories) take
        # effect even for rows whose CSV column predates the change.
        product = canonical_product_name(r.get("product_name", ""))
        stores_seen.add(store)
        val, unit = normalized(r)
        ts = parse_ts(r.get("scraped_at", ""))
        if val is not None and unit:
            series[(product, store, unit)].append((ts, val))
        if not is_active_offer(r.get("date_range", ""), today_date):
            continue
        lp = number(r.get("loyalty_price", ""))
        disp_val = lp if is_true(r.get("loyalty_required", "")) and lp is not None else val
        s, en = parsed_date_range(r.get("date_range", ""))
        end = _iso(en)
        source_identity = r.get("product_id", "") or r.get("product_name", "")
        offer_key = (
            product,
            category,
            store,
            source_identity,
            str(disp_val if disp_val is not None else ""),
            unit,
        )
        existing = current_entries.get(offer_key)
        if existing is None or ts > existing["_scraped_at"]:
            current_entries[offer_key] = {
                "product": product,
                "category": category,
                "store": store,
                "val": disp_val if disp_val is not None else val,
                "unit": unit,
                "date_range": r.get("date_range", ""),
                "raw_name": r.get("product_name", product),
                "product_id": r.get("product_id", ""),
                "image_url": r.get("image_url", ""),
                "_end": end,
                "_scraped_at": ts,
            }

    # Main-page rows group product variants under one canonical name. After
    # preserving distinct source offers above, collapse only visually identical
    # lines (same canonical product, store, price, unit, and date range). This
    # keeps genuinely different future prices/windows while avoiding three
    # indistinguishable "Rajčata cherry · Albert · 139.60/kg" rows.
    source_entries = current_entries
    current_entries = {}
    for entry in source_entries.values():
        visual_key = (
            entry["product"],
            entry["category"],
            entry["store"],
            str(entry["val"] if entry["val"] is not None else ""),
            entry["unit"],
            entry["date_range"],
        )
        existing = current_entries.get(visual_key)
        if existing is None or entry["_scraped_at"] > existing["_scraped_at"]:
            current_entries[visual_key] = entry

    products: dict[str, list[dict]] = defaultdict(list)
    for e in current_entries.values():
        products[e["product"]].append(e)

    # Combine local exact observations with Kupi's product-level graph history.
    # Both are unit-aware: a canonical product can have kg and piece offers, but
    # a single sparkline must never mix the two price bases.
    local_day_min: dict[tuple[str, str], dict[date, float]] = defaultdict(dict)
    for (product, _store, unit), pts in series.items():
        for ts, val in pts:
            observed = ts.date()
            current = local_day_min[(product, unit)].get(observed)
            if current is None or val < current:
                local_day_min[(product, unit)][observed] = val
    graph_day_min = kupi_graph_lowest_series(today=today_date)
    lowest_series: dict[str, list[tuple[datetime, float]]] = {}
    for product, entries in products.items():
        active_units = {entry["unit"] for entry in entries if entry.get("unit")}
        trend_unit = "kg" if "kg" in active_units else ("ks" if "ks" in active_units else "")
        if not trend_unit:
            continue
        daily = merge_daily_trends(
            graph_day_min.get((product, trend_unit), {}),
            local_day_min.get((product, trend_unit), {}),
        )
        if daily:
            lowest_series[product] = sorted(
                [(datetime(day.year, day.month, day.day), value) for day, value in daily.items()],
                key=lambda point: point[0],
            )

    # Collect the most-recent active offer per distinct retailer label so each
    # exact-SKU page links back to its canonical product and shows its own
    # discount line. ``products`` already groups by canonical name; here we index
    # the raw offer label for the exact variant pages.
    exact_rows = {}
    for _r in rows:
        _name = _r.get("product_name", "")
        if not _name:
            continue
        _slug = slugify(_name)
        existing = exact_rows.get(_slug)
        _ts = parse_ts(_r.get("scraped_at", ""))
        if existing is None or _ts > existing.get("_ts", ""):
            _normalized_value, _normalized_unit = normalized(_r)
            exact_rows[_slug] = {
                "raw_name": _name,
                "pretty": display_name(canonical_product_name(_name), _name),
                "canonical_product_name": canonical_product_name(_name),
                "product_id": _r.get("product_id", ""),
                "image_url": _r.get("image_url", ""),
                "store": _r.get("store", ""),
                "val": _normalized_value,
                "unit": _normalized_unit,
                "date_range": _r.get("date_range", ""),
                "_ts": _ts,
            }
    # Map the exact-label nutrition cache (keyed by exact:<label>) into the
    # slug-keyed form that write_exact_product_pages expects.
    exact_nutrition_by_slug = {}
    for _kk, _vv in exact_nutrition.items():
        if _kk.startswith("exact:"):
            exact_nutrition_by_slug[slugify(_kk[6:])] = _vv
    write_product_pages(products, nutrition)
    write_exact_product_pages(exact_nutrition_by_slug, exact_rows)

    # Store brand colors/logos come from the single STORE_BRAND registry in
    # scrapers.common (store_color / store_logo helpers).
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
        has_nutrition = (
            nutrition_entry.get("status") == "found"
            and any(
                isinstance(value, dict) and value.get("value") is not None
                for value in nutrition_values.values()
            )
        )
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
        img_path = cache_image(
            first.get("product_id", product), first.get("image_url", "")
        )
        img_tag = (
            f'<img class="thumb" src="{img_path}" width="40" height="40" alt="" aria-hidden="true">'
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
        for entry_index, e in enumerate(entries):
            line_id = f"{product}:{entry_index}"
            logo = store_logo(e["store"])
            color = store_color(e["store"])
            dr = e.get("date_range", "") or ""
            s, en = parsed_date_range(dr)
            start_date, end_date = _iso(s), _iso(en)
            if start_date and (union_start is None or s < union_start):
                union_start = s
            if end_date and (union_end is None or en > union_end):
                union_end = en
            # Price cell is only meaningful when we have a parsed value.
            v = e["val"]
            if v is not None:
                pp_cells.append(
                    f'<div class="ppcell" data-store="{esc(e["store"])}" data-category="{esc(e["category"])}" data-line="{esc(line_id)}">'
                    f'{logo}<span class="pprice">{v:.2f} / {e["unit"]}</span></div>'
                )
            # Render date cells only for a fully parseable range. A malformed
            # upstream value remains visible as a price row rather than crashing
            # the static-site build.
            if start_date and end_date:
                day_items = []
                for day_index, day in enumerate(timeline_days):
                    active = start_date <= day <= end_date
                    classes = "day"
                    if active:
                        classes += " active"
                    day_items.append((
                        day_index,
                        f'<span class="{classes}" data-date="{day.isoformat()}" title="{esc(e["store"])} · {fmt_cz(day.isoformat())}">{_DOW1_CZ[day.weekday()]}</span>',
                    ))
                day_html = group_weeks(day_items)
                date_cells.append(
                    f'<div class="dcell" data-store="{esc(e["store"])}" data-category="{esc(e["category"])}" data-line="{esc(line_id)}" '
                    f'data-s="{esc(s)}" data-e="{esc(en)}" '
                    f'aria-label="{esc(e["store"])}: {esc(fmt_cz(dr))}" '
                    f'style="--store-color:{color}"><div class="timeline">{day_html}</div></div>'
                )
        spark_html = sparkline(lowest_series.get(product, [])) or '<span class="dim">1 run</span>'
        table_rows.append(
            f"<tr data-start=\"{esc(union_start or '')}\" data-end=\"{esc(union_end or '')}\" data-has-nutrition=\"{'true' if has_nutrition else 'false'}\">"
            f'<td class="prod"><a class="product-link" href="products/{esc(product)}.html" aria-label="{esc(pretty)}">'
            f'{img_tag}<span class="pname">{esc(pretty)}</span></a></td>'
            f'<td class="pricelist">{"".join(pp_cells)}</td>'
            f'<td class="drange">{"".join(date_cells)}</td>'
            f'<td class="spark"><div class="sparkline">{spark_html}</div></td>'
            f"</tr>"
        )

    last_run = last_run or "unknown"
    n_products = len(table_rows)
    n_stores = len(stores_seen)
    active_categories = {entry.get("category", "Ovoce a zelenina") for entry in current_entries.values()}
    ordered_categories = [label for label, _slug in KUPI_FOOD_CATEGORIES if label in active_categories]
    ordered_categories.extend(sorted(active_categories - set(ordered_categories)))
    category_options = '<option value="all">All products</option>' + "".join(
        f'<option value="{esc(category)}">{esc(category)}</option>'
        for category in ordered_categories
    )
    ranking_data = []
    ranking_labels = {}
    ranking_rdas = {}
    for product, entries in products.items():
        nutri = nutrition.get(product, {})
        vals = nutri.get("values", {}) if nutri.get("status") == "found" else {}
        source_image = str((entries[0] if entries else {}).get("image_url", "")).lower()
        if (not source_image or any(marker in source_image for marker in ("no-image", "no_image", "/no_img/", "placeholder"))):
            vals = {}
        if not vals:
            continue
        pretty = display_name(product, (entries[0] if entries else {}).get("raw_name", product))
        for label, nutrient in vals.items():
            if isinstance(nutrient, dict) and isinstance(nutrient.get("value"), (int, float)) and nutrient["value"] > 0:
                ranking_labels[label] = nutrient.get("unit", "")
                rda = rda_amount_in_unit(label, nutrient.get("unit", ""))
                if rda is not None:
                    ranking_rdas[label] = rda
        for entry in entries:
            price = number(entry.get("val", ""))
            if price is None or price <= 0:
                continue
            start, end = parsed_date_range(entry.get("date_range", ""))
            weight = explicit_weight_grams(entry.get("raw_name", ""))
            estimated = False
            if weight is None and entry.get("unit") == "ks":
                weight = estimated_piece_weight_grams(product)
                estimated = weight is not None
            if entry.get("unit") == "kg":
                basis = "kg"
                nutrient_factor = 10.0
            elif entry.get("unit") == "ks" and weight:
                basis = f"~{weight:g}g" if estimated else f"{weight:g}g"
                nutrient_factor = weight / 100.0
            else:
                # A piece price without a verified weight cannot be compared
                # fairly with per-kilogram offers, so it is omitted by default.
                continue
            ranking_data.append({
                # Nutrition remains associated with the canonical product, while
                # the ranking labels each offer with the store's exact name.
                "product": entry.get("raw_name") or pretty, "url": f"products/{product}.html", "image": cache_image(entry.get("product_id", product), entry.get("image_url", "")), "store": entry.get("store", ""), "category": entry.get("category", "Ovoce a zelenina"), "storeLogo": store_logo(entry.get("store", "")), "storeColor": store_color(entry.get("store", "")),
                "price": price, "basis": basis, "factor": nutrient_factor,
                "start": start or "", "end": end or "",
                "values": {label: nutrient.get("value") for label, nutrient in vals.items()
                           if isinstance(nutrient, dict) and isinstance(nutrient.get("value"), (int, float)) and nutrient["value"] > 0},
                "rdas": {label: rda_amount_in_unit(label, nutrient.get("unit", "")) for label, nutrient in vals.items()
                         if isinstance(nutrient, dict) and rda_amount_in_unit(label, nutrient.get("unit", "")) is not None},
            })
    ranking_options = sorted(ranking_labels)
    for label in ("Omega-3 fat", "Omega-6 fat"):
        ranking_labels.setdefault(label, "g")
    ranking_options = sorted(ranking_labels)
    ranking_groups = {
        "Calories & macros": [label for label in ranking_options if label in {"Calories", "Protein", "Carbs", "Fat", "Fiber", "Omega-3 fat", "Omega-6 fat"}],
        "Vitamins": [label for label in ranking_options if label.startswith("Vitamin ")],
        "Minerals": [label for label in ranking_options if label in {"Calcium", "Iron", "Magnesium", "Phosphorus", "Potassium", "Zinc", "Copper", "Manganese", "Selenium", "Sodium"}],
    }
    ranking_groups = {group: labels for group, labels in ranking_groups.items() if labels}
    ranking_groups_json = json.dumps(ranking_groups, ensure_ascii=False).replace("</", "<\\/")
    ranking_dates_json = json.dumps([d.isoformat() for d in timeline_days], ensure_ascii=False)
    ranking_dow_json = json.dumps([_DOW1_CZ[d.weekday()] for d in timeline_days], ensure_ascii=False)
    ranking_date_labels_json = json.dumps([fmt_cz(d.isoformat()) for d in timeline_days], ensure_ascii=False)
    ranking_json = json.dumps(ranking_data, ensure_ascii=False).replace("</", "<\\/")
    ranking_units_json = json.dumps(ranking_labels, ensure_ascii=False).replace("</", "<\\/")
    ranking_rdas_json = json.dumps(ranking_rdas, ensure_ascii=False).replace("</", "<\\/")
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
        s, en = parsed_date_range(e.get("date_range", ""))
        all_ends.extend(d for d in (s, en) if _iso(d))
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
<script>try {{ const t=localStorage.getItem('grocery-theme'); if (t==='light' || t==='dark') {{ document.documentElement.setAttribute('data-theme', t); document.documentElement.style.colorScheme=t; document.documentElement.style.backgroundColor=t==='dark'?'#1a1b26':'#eff1f5'; }} }} catch (e) {{}}</script>
<style>
* {{ box-sizing: border-box; }}
html {{ background: var(--bg); color-scheme: dark light; scrollbar-gutter: stable; }}
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
body {{ font-family: -apple-system, system-ui, sans-serif; max-width:900px; margin:0 auto;
  background: var(--bg); color: var(--fg); padding:24px; }}
/* Keep the page aligned to the viewport. On narrow screens .table-scroll owns
   the horizontal scroll so the fixed, full-width timeline stays intact. */
.page {{ width:100%; max-width:100%; margin:0 auto; }}
.table-scroll {{ width:100%; overflow-x:auto; }}
h1 {{ font-size: 1.3rem; margin: 0 0 4px; }}
.meta {{ color: var(--muted); font-size: .85rem; margin-bottom: 14px; }}
table {{ width:100%; min-width:837px; border-collapse: collapse; font-size: .9rem; table-layout: fixed; }}
/* The two-week timeline is an information-bearing column, not flexible
   decoration: it must never be squeezed below its 14-day width. Narrow
   screens scroll the table rather than clipping or compressing its days. */
th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
  vertical-align: top; overflow: hidden; }}
th {{ color: var(--muted); font-weight: 600; cursor: pointer; user-select: none;
  position: sticky; top: 0; background: var(--th-bg); }}
/* Fixed column widths keep the header stable whether the body is full, empty,
   or showing a date with no matches — no reflow / left-shift. The product column
   is wide enough to show full Czech product names without truncation. */
/* Keep enough inline room for all 14 day cells (including week gap and cell
   padding). This is explicit instead of depending on the browser's leftover
   table width, which previously made the timeline shrink at some viewports. */
th[data-k="prod"] {{ width: 220px; }}
th[data-k="pricelist"] {{ width: 120px; }}
th[data-k="drange"] {{ width:276px; min-width:276px; max-width:276px; }}
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
/* Nutrition-only is deliberately a pure CSS filter. It avoids re-running the
   expensive per-store/date update over every offer row on each button click. */
#t.nutrition-only tbody tr[data-has-nutrition="false"] {{ display: none !important; }}
/* When a product is fully filtered out (every store line muted or out of the
   selected date range) under normal filtering, fade its name to match the
   .datedim/.muted level so the whole row dims uniformly. */
td.prod.prodfade {{ opacity: .28; transition: opacity .15s; }}
.legend {{ display: flex; gap: 12px; margin: 6px 0 14px; font-size: .82rem; }}
.legend .chip {{ display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  padding: 4px 10px; border-radius: 999px; border: 1px solid var(--track); font-weight: 600;
  background: var(--surface); user-select: none; transition: opacity .15s, background .15s; }}
.legend .chip .logo {{ width: 18px; height: 18px; }}
.legend .chip.off {{ opacity: .35; background: var(--chip-off-bg); border-color: var(--surface); }}
.legend .chip:hover {{ border-color: var(--dim); }}
.toggle {{ cursor: pointer; padding: 6px 14px; border-radius: 999px;
  border: 1px solid var(--track); background: var(--toggle-bg); color: var(--fg); font-size: .85rem;
  user-select: none; transition: background .15s, border-color .15s; }}
.toggle.on {{ background: var(--accent); color: var(--thumb-border); border-color: var(--accent); }}
.toggles {{ display: flex; align-items: center; gap: 8px; }}
.controls {{ display:flex; align-items:center; justify-content:space-between; gap:8px; width:100%; }}
.topbar {{ margin: 0 0 10px; }}
.topbar h1 {{ margin: 0 0 10px; font-size: 1.5rem; }}
.theme {{ display: inline-flex; border: 1px solid var(--track); border-radius: 999px; overflow: hidden; }}
.theme button {{ cursor: pointer; border: 0; background: var(--toggle-bg); color: var(--fg);
  font-size: .85rem; padding: 6px 12px; }}
.theme button + button {{ border-left: 1px solid var(--track); }}
.theme button.active {{ background: var(--accent); color: var(--thumb-border); font-weight: 600; }}
.filter-controls {{ display:flex; align-items:center; gap:12px; margin:0 0 10px; }}
.category-filter {{ display:flex; align-items:center; gap:8px; font-size:.85rem; }}
.category-filter select {{ background:var(--toggle-bg); color:var(--fg); border:1px solid var(--track); border-radius:6px; padding:5px 8px; }}
.timeline-head {{ display: flex; flex-direction: column; gap: 3px; }}
.date-label {{ color: var(--muted); font-weight: 600; font-size: .85rem; }}
.ranking-card {{ overflow-x:auto; }}
.ranking-card[hidden] {{ display:none; }}
.ranking-heading {{ display:flex; align-items:center; justify-content:space-between; gap:12px; }}
.ranking-heading h2 {{ margin:0; }}
.ranking-heading select {{ background:var(--toggle-bg); color:var(--fg); border:1px solid var(--track); border-radius:6px; padding:5px 8px; }}
.ranking-help {{ margin:8px 0; }}
.ranking-product {{ display:inline-flex; align-items:center; gap:8px; }}
.ranking-product {{ color:inherit; text-decoration:none; font-weight:600; }}
.ranking-product img {{ width:36px; height:36px; object-fit:contain; border-radius:6px; background:var(--surface); vertical-align:middle; }}
.ranking-store {{ display:inline-flex; align-items:center; gap:6px; font-weight:600; cursor:pointer; }}
.ranking-store .logo {{ width:18px; height:18px; }}
#rankingTable td:nth-child(4) {{ font-weight:700; }}
#rankingTable td {{ vertical-align:middle; }}
#rankingTable {{ width:100%; max-width:100%; table-layout:fixed; }}
#rankingTable th:nth-child(2), #rankingTable td:nth-child(2) {{ text-align:right; }}
#rankingTable th:nth-child(1), #rankingTable td:nth-child(1) {{ width:220px; }}
#rankingTable th:nth-child(2), #rankingTable td:nth-child(2) {{ width:150px; white-space:nowrap; }}
#rankingTable th:nth-child(4), #rankingTable td:nth-child(4) {{ width:150px; white-space:nowrap; }}
.ranking-store {{ justify-content:flex-end; }}
.ranking-datedim {{ opacity:.28; transition:opacity .15s; }}
.ranking-storedim {{ opacity:.3; transition:opacity .15s; }}
.ranking-drange {{ white-space:nowrap; }}
.ranking-drange .timeline {{ display:inline-flex; gap:3px; align-items:center; width:max-content; }}
.ranking-drange .day {{ width:13px; height:13px; }}
.ranking-drange .day.week-start {{ margin-left:5px; }}
.ranking-drange .day.sel {{ position:relative; z-index:2; outline:2px solid var(--fg); outline-offset:1px; border:2px solid var(--fg); box-shadow:inset 0 0 0 1px var(--surface); }}
@media (max-width:700px) {{
  body {{ padding:12px; }}
  .topbar h1 {{ font-size:1.25rem; }}
  .controls {{ width:100%; flex-wrap:nowrap; justify-content:space-between; gap:4px; margin-left:0; }}
  .theme, .toggles {{ flex-wrap:nowrap; gap:4px; }}
  .theme button, .toggle {{ padding:4px 6px; font-size:.72rem; white-space:nowrap; }}
  .legend {{ flex-wrap:wrap; gap:6px; }}
  .page > .card {{ overflow-x:auto; }}
  #t {{ min-width:837px; }}
  #rankingTable {{ min-width:760px; }}
  .ranking-heading {{ flex-wrap:wrap; }}
  .ranking-heading > div {{ display:flex; flex-wrap:wrap; gap:6px; width:100%; }}
  .ranking-heading select {{ flex:1 1 140px; min-width:0; }}
}}
</style></head>
<body>
<div class="page">
<div class="topbar">
  <h1>🪸 Grocery Prices</h1>
  <div class="controls">
    <div class="theme" id="themeSwitch">
      <button data-theme="light">Light</button>
      <button data-theme="dark">Dark</button>
      <button data-theme="system">System</button>
    </div>
    <div class="toggles">
      <span class="toggle" id="hideToggle" title="Hide rows whose discounts fall outside the selected date range">Hide irrelevant</span>
      <span class="toggle" id="rankToggle">Rank by nutrient</span>
    </div>
  </div>
</div>
<div class="filter-controls"><div class="category-filter"><label for="categoryFilter">Category</label><select id="categoryFilter">{category_options}</select></div><button class="toggle" id="nutritionFilter" type="button" aria-pressed="false">Nutrition only</button></div>
<div class="card ranking-card" id="rankingCard" hidden>
  <div class="ranking-heading"><h2>Best nutrient value</h2><div><select id="rankingCategory"></select> <select id="rankingNutrient"></select></div></div>
  <p class="muted ranking-help">Top 10 discounts by lowest cost for 100% RDA. Per-piece offers are included only when their package weight is explicit.</p>
  <table id="rankingTable"><thead><tr><th>Product</th><th>Price per 100% RDA</th><th>Discount days</th><th>Price</th></tr></thead><tbody></tbody></table>
</div>
<div class="meta">Last run: {esc(last_run)} &middot; {n_products} products &middot;
 {n_stores} stores</div>
<div class="legend">
  <span class="chip" data-store="Lidl">{store_logo("Lidl")} Lidl</span>
  <span class="chip" data-store="Tesco">{store_logo("Tesco")} Tesco</span>
  <span class="chip" data-store="Albert">{store_logo("Albert")} Albert</span>
  <span class="chip" data-store="Billa">{store_logo("Billa")} Billa</span>
</div>
<div class="table-scroll"><table id="t">
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
{empty_notice}</tbody></table></div>
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
let nutritionOnly = false;
const categoryFilter = document.getElementById('categoryFilter');
const FILTERS_KEY = 'grocery-filters';
let savedFilterState = null;
try {{
  const raw = localStorage.getItem(FILTERS_KEY);
  if (raw) savedFilterState = JSON.parse(raw);
}} catch (e) {{}}
function saveFilters() {{
  try {{
    localStorage.setItem(FILTERS_KEY, JSON.stringify({{
      hiddenStores: [...hidden],
      category: categoryFilter.value,
      hideIrrelevant,
      nutritionOnly,
      rankingVisible: !rankingCard.hidden,
      rankingCategory: rankingCategory.value,
      rankingNutrient: rankingNutrient.value,
      rangeStartOffset: offS,
      rangeEndOffset: offE,
    }}));
  }} catch (e) {{}}
}}
const chips = [...document.querySelectorAll('.legend .chip')];
const rankingData = {ranking_json};
const rankingUnits = {ranking_units_json};
const rankingGroups = {ranking_groups_json};
const rankingDates = {ranking_dates_json};
const rankingDow = {ranking_dow_json};
const rankingDateLabels = {ranking_date_labels_json};
const rankingCard = document.getElementById('rankingCard');
const rankingTableBody = document.querySelector('#rankingTable tbody');
const rankingCategory = document.getElementById('rankingCategory');
const rankingNutrient = document.getElementById('rankingNutrient');
Object.keys(rankingGroups).forEach(group => rankingCategory.add(new Option(group, group)));
function refreshRankingNutrients() {{
  rankingNutrient.replaceChildren(...rankingGroups[rankingCategory.value].map(label => new Option(label.startsWith('Vitamin ') ? label.replace('Vitamin ', '').replace(/ \\(.*/, '') : label.replace(/ fat$/, ''), label)));
  updateRanking();
}}
rankingCategory.onchange = () => {{ refreshRankingNutrients(); saveFilters(); }};
refreshRankingNutrients();
function updateRanking() {{
  if (!rankingCard || rankingCard.hidden || !rankingNutrient.value) return;
  const label = rankingNutrient.value;
  const ranked = rankingData.filter(item => (categoryFilter.value === 'all' || item.category === categoryFilter.value) && (!hideIrrelevant || !hidden.has(item.store)) && (!hideIrrelevant || (item.start <= rangeEnd && (item.end || item.start) >= rangeStart)))
    .map(item => ({{ ...item, amount: item.values[label] * item.factor, rdaCost: item.price * item.rdas[label] / (item.values[label] * item.factor), datedim: !(item.start <= rangeEnd && (item.end || item.start) >= rangeStart), storedim: hidden.has(item.store) }}))
    .filter(item => Number.isFinite(item.amount) && item.amount > 0 && Number.isFinite(item.rdas[label]) && item.rdas[label] > 0)
    .sort((a, b) => a.rdaCost - b.rdaCost)
    .slice(0, 10);
  rankingTableBody.innerHTML = ranked.map(item => `<tr class="${{(item.datedim && !hideIrrelevant ? 'ranking-datedim ' : '') + (item.storedim && !hideIrrelevant ? 'ranking-storedim' : '')}}"><td><a class="ranking-product" href="${{item.url}}"><img src="${{item.image}}" width="36" height="36" alt="" aria-hidden="true"><span>${{item.product}}</span></a></td><td><span class="ranking-store" data-store="${{item.store}}" aria-label="${{item.store}}"><span>${{(item.price * item.rdas[label] / item.amount).toFixed(2)}} Kč</span>${{item.storeLogo}}</span></td><td class="ranking-drange" style="--store-color:${{item.storeColor}}"><div class="timeline">${{rankingDates.map((day, i) => `<span class="day${{new Date(day + 'T00:00:00Z').getUTCDay() === 1 ? ' week-start' : ''}} ${{item.start <= day && (item.end || item.start) >= day ? 'active' : ''}}" data-date="${{day}}" title="${{item.store}} · ${{rankingDateLabels[i]}}">${{rankingDow[i]}}</span>`).join('')}}</div></td><td>${{item.price.toFixed(2)}} Kč / ${{item.basis}}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">No matching RDA data</td></tr>';
  rankingTableBody.querySelectorAll('.ranking-store').forEach(store => store.onclick = () => setStoreHidden(store.dataset.store, !hidden.has(store.dataset.store)));
}}
document.getElementById('rankToggle').onclick = () => {{
  rankingCard.hidden = !rankingCard.hidden;
  document.getElementById('rankToggle').classList.toggle('on', !rankingCard.hidden);
  updateRanking();
  saveFilters();
}};
rankingNutrient.onchange = () => {{ updateRanking(); saveFilters(); }};
categoryFilter.onchange = () => {{ applyFilters(); saveFilters(); }};
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
  document.querySelectorAll('#t .day, #rankingTable .day').forEach(d => {{
    const ds = d.dataset.date;
    const owner = d.closest('.dcell');
    const ownerStore = owner && owner.dataset.store;
    const ownerCategory = owner && owner.dataset.category;
    const muted = !!(ownerStore && hidden.has(ownerStore));
    const categoryHidden = !!(ownerCategory && categoryFilter.value !== 'all' && ownerCategory !== categoryFilter.value);
    const on = !!ds && !muted && !categoryHidden && (ds === rangeStart || ds === rangeEnd);
    d.classList.toggle('sel', on);
  }});

  rows.forEach(r => {{
    // Pair each rendered offer's price cell (.ppcell) and date cell (.dcell)
    // by data-line, not by store. A store can now have several overlapping
    // discounts for the same product inside the two-week window.
    const lines = [];
    const ppByLine = {{}};
    r.querySelectorAll('.ppcell').forEach(c => {{ ppByLine[c.dataset.line] = c; }});
    r.querySelectorAll('.dcell').forEach(c => {{
      const st = c.dataset.store;
      lines.push({{ store: st, pp: ppByLine[c.dataset.line] || null, dc: c }});
    }});
    let visibleStores = 0;
    let anyLineShown = false;
    let shown = 0;   // lines actually visible under current filters (not muted AND in window)
    lines.forEach(L => {{
      const st = L.store;
      const muted = !!(st && hidden.has(st));
      const categoryHidden = categoryFilter.value !== 'all' && L.dc.dataset.category !== categoryFilter.value;
      if (L.pp) L.pp.classList.toggle('muted', muted);
      L.dc.classList.toggle('muted', muted);
      if (!muted && !categoryHidden) visibleStores++;
      const ds = L.dc.dataset.s, de = L.dc.dataset.e;
      const hasDate = !!ds;
      const overlap = hasDate && ds <= rangeEnd && (de || ds) >= rangeStart;
      const inWin = hasDate ? overlap : true;   // a line with no date isn't date-filtered
      if (!muted && !categoryHidden && inWin) shown++;
      const dim = !muted && !categoryHidden && hasDate && !overlap;
      if (L.pp) L.pp.classList.toggle('datedim', dim);
      L.dc.classList.toggle('datedim', dim);
      const lineShown = !categoryHidden && (!hideIrrelevant || (!muted && overlap));
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
    const matchesNutrition = !nutritionOnly || r.dataset.hasNutrition === 'true';
    const show = anyLineShown && matchesNutrition;
    r.style.display = show ? '' : 'none';
  }});
  updateRanking();
}}
// Toggle a store's visibility (used by both the legend chips and the store
// icons in the Price column). Reflects state on the chip (.off) and on every
// .ppcell belonging to that store (.muted look) via applyFilters().
function setStoreHidden(s, hide) {{
  if (hide) {{ hidden.add(s); }} else {{ hidden.delete(s); }}
  const chip = chips.find(c => c.dataset.store === s);
  if (chip) chip.classList.toggle('off', hide);
  applyFilters();
  saveFilters();
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
  saveFilters();
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
    const days = Array.from(document.querySelectorAll('#t .day, #rankingTable .day'));
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
    const sq = document.querySelectorAll('#t .day, #rankingTable .day')[i];
    return sq ? isoToOffset(sq.dataset.date) : -1;
  }};
  const ondown = (e) => {{
    if (e.button !== 0) return;   // primary/left only; ignore middle (1) / right (2)
    // Whole date-column cell is the hit target (mirrors the old header picker):
    // gaps between squares AND the slack to the right of the last square all
    // Voronoi-map onto the nearest day, so nothing in the column is dead.
    const cell = e.target.closest('td.drange, td.ranking-drange');
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
  document.addEventListener('pointerdown', ondown);
  document.addEventListener('pointermove', onmove);
  document.addEventListener('pointerup', onup);
  document.addEventListener('pointercancel', onup);
}})();

// "Hide irrelevant" toggle: opt-in whole-row hiding for discounts that
// fall outside the selected date range. Default OFF — every row stays visible
// (dimmed where out of range) so the table never jumps.
const hideBtn = document.getElementById('hideToggle');
hideBtn.onclick = () => {{
  hideIrrelevant = !hideIrrelevant;
  hideBtn.classList.toggle('on', hideIrrelevant);
  applyFilters();
  saveFilters();
}};

const nutritionFilterBtn = document.getElementById('nutritionFilter');
nutritionFilterBtn.onclick = () => {{
  nutritionOnly = !nutritionOnly;
  nutritionFilterBtn.classList.toggle('on', nutritionOnly);
  nutritionFilterBtn.setAttribute('aria-pressed', String(nutritionOnly));
  t.classList.toggle('nutrition-only', nutritionOnly);
  saveFilters();
}};

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

// Restore the dashboard filters after all controls and their handlers exist.
// Invalid or stale values are ignored so a changed build cannot get stuck.
if (savedFilterState && typeof savedFilterState === 'object') {{
  if ([...categoryFilter.options].some(o => o.value === savedFilterState.category))
    categoryFilter.value = savedFilterState.category;
  if (Array.isArray(savedFilterState.hiddenStores)) {{
    savedFilterState.hiddenStores.forEach(s => hidden.add(s));
    chips.forEach(chip => chip.classList.toggle('off', hidden.has(chip.dataset.store)));
  }}
  hideIrrelevant = savedFilterState.hideIrrelevant === true;
  nutritionOnly = savedFilterState.nutritionOnly === true;
  hideBtn.classList.toggle('on', hideIrrelevant);
  nutritionFilterBtn.classList.toggle('on', nutritionOnly);
  nutritionFilterBtn.setAttribute('aria-pressed', String(nutritionOnly));
  t.classList.toggle('nutrition-only', nutritionOnly);
  if (Number.isInteger(savedFilterState.rangeStartOffset) && Number.isInteger(savedFilterState.rangeEndOffset)) {{
    offS = Math.max(0, Math.min(SPAN_MAX, savedFilterState.rangeStartOffset));
    offE = Math.max(offS, Math.min(SPAN_MAX, savedFilterState.rangeEndOffset));
  }}
  rankingCard.hidden = savedFilterState.rankingVisible !== true;
  document.getElementById('rankToggle').classList.toggle('on', !rankingCard.hidden);
  if ([...rankingCategory.options].some(o => o.value === savedFilterState.rankingCategory))
    rankingCategory.value = savedFilterState.rankingCategory;
  refreshRankingNutrients();
  if ([...rankingNutrient.options].some(o => o.value === savedFilterState.rankingNutrient))
    rankingNutrient.value = savedFilterState.rankingNutrient;
  syncView();
}}
</script>
</body></html>"""
    return html


def main() -> None:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    fallback_image()
    html = build()
    INDEX_HTML.write_text(html, encoding="utf-8")
    print(f"Wrote {INDEX_HTML.name} ({len(html)} bytes) into {SITE_DIR.name}/.")


if __name__ == "__main__":
    main()
