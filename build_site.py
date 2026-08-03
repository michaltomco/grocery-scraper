"""Generate a self-contained HTML dashboard from the scraped grocery data.

Reads history.csv (cumulative) and produces index.html:
  - summary header (last run, # items, # stores)
  - sortable table: each product, all stores with normalized /kg or /ks price,
    best store highlighted green, worst red, a mini price-bar per store,
    and a price-trend sparkline (populates as more daily runs accumulate)
No third-party deps; pure stdlib. Output is a single file with inline CSS/JS.
"""
import csv
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from scrapers.common import read_csv
except ModuleNotFoundError:
    from common import read_csv

ROOT = Path(__file__).resolve().parent
HISTORY_CSV = ROOT / "history.csv"
SITE_DIR = ROOT / "site"
IMG_DIR = SITE_DIR / "img"
INDEX_HTML = SITE_DIR / "index.html"

# Download each product image exactly once, keyed by product_id. Already-cached
# files are never re-fetched. Returns a web path relative to the site root.
def cache_image(product_id: str, image_url: str) -> str:
    if not image_url:
        return ""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(image_url.split("?")[0]).suffix or ".jpg"
    dest = IMG_DIR / f"{product_id}{ext}"
    if not dest.exists():
        try:
            req = urllib.request.Request(
                image_url,
                headers={"User-Agent": "Mozilla/5.0 (grocery-scraper)"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp, dest.open("wb") as out:
                out.write(resp.read())
        except Exception:
            return ""
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


def build() -> str:
    if not HISTORY_CSV.exists():
        return "<p>No history.csv — run the scraper first.</p>"

    rows = read_csv(HISTORY_CSV)
    if not rows:
        return "<p>history.csv is empty.</p>"

    # current run = latest RUN DATE (YYYY-MM-DD). All three scrapers run in the
    # same daily cron, so a run spans several scraped_at timestamps within one
    # day — grouping by date keeps Albert/Lidl/Tesco together instead of
    # dropping all but the last-finished store.
    run_dates = sorted({r.get("scraped_at", "")[:10] for r in rows if r.get("scraped_at")}, reverse=True)
    current_run = run_dates[0] if run_dates else ""
    prev_run = run_dates[1] if len(run_dates) > 1 else None

    # per (product, store) -> current entry; per (product, store) -> time series
    current_entries: dict[tuple[str, str], dict] = {}
    series: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    stores_seen: set[str] = set()
    for r in rows:
        store = r.get("store", "")
        product = r.get("canonical_product_name", "")
        stores_seen.add(store)
        val, unit = normalized(r)
        ts = parse_ts(r.get("scraped_at", ""))
        if val is not None:
            series[(product, store)].append((ts, val))
        if r.get("scraped_at", "").startswith(current_run):
            # keep cheapest normalized entry for the store (some items list twice)
            lp = number(r.get("loyalty_price", ""))
            disp_val = val
            if r.get("loyalty_required", "").strip().lower() in {"true", "1", "yes", "y"} and lp is not None:
                disp_val = lp
            existing = current_entries.get((product, store))
            if existing is None or (disp_val is not None and disp_val < existing["val"]):
                current_entries[(product, store)] = {
                    "product": product, "store": store,
                    "val": disp_val if disp_val is not None else val,
                    "unit": unit, "date_range": r.get("date_range", ""),
                    "product_id": r.get("product_id", ""),
                    "image_url": r.get("image_url", ""),
                }

    products: dict[str, list[dict]] = defaultdict(list)
    for (product, store), e in current_entries.items():
        products[product].append(e)

    # Store brand colors (not price-ranked): Lidl=yellow, Tesco=red, Albert=blue.
    STORE_COLOR = {"Lidl": "#facc15", "Tesco": "#f87171", "Albert": "#60a5fa"}
    # Small brand-colored logo per store (letter badge on brand color).
    STORE_LOGO = {
        "Lidl": '<svg class="logo" viewBox="0 0 24 24" role="img" aria-label="Lidl"><rect x="2" y="2" width="20" height="20" rx="5" fill="#facc15"/><text x="12" y="16" font-size="12" font-weight="700" text-anchor="middle" fill="#0f172a">L</text></svg>',
        "Tesco": '<svg class="logo" viewBox="0 0 24 24" role="img" aria-label="Tesco"><rect x="2" y="2" width="20" height="20" rx="5" fill="#f87171"/><text x="12" y="16" font-size="12" font-weight="700" text-anchor="middle" fill="#0f172a">T</text></svg>',
        "Albert": '<svg class="logo" viewBox="0 0 24 24" role="img" aria-label="Albert"><rect x="2" y="2" width="20" height="20" rx="5" fill="#60a5fa"/><text x="12" y="16" font-size="12" font-weight="700" text-anchor="middle" fill="#0f172a">A</text></svg>',
    }
    table_rows = []
    for product in sorted(products):
        for e in sorted(products[product], key=lambda x: (x["val"] is None, x["val"] or 0)):
            v = e["val"]
            if v is None:
                continue
            store = e["store"]
            logo = STORE_LOGO.get(store, "")
            color = STORE_COLOR.get(store, "#64748b")
            # trend = this product+store price series over time
            spark = sparkline(series.get((product, store), []))
            img_path = cache_image(e.get("product_id", product), e.get("image_url", ""))
            img_tag = (
                f'<img class="thumb" src="{img_path}" alt="{esc(product)}" loading="lazy">'
                if img_path else '<span class="dim">no img</span>'
            )
            table_rows.append(
                f'<tr style="border-left:4px solid {color}">'
                f'<td class="prod">{img_tag}<span class="pname">{esc(product)}</span></td>'
                f'<td class="store">{logo}</td>'
                f'<td class="num">{v:.2f}/{e["unit"]}</td>'
                f'<td class="drange">{esc(e["date_range"]) or "&mdash;"}</td>'
                f'<td class="spark">{spark or "<span class=dim>1 run</span>"}</td>'
                f"</tr>"
            )

    last_run = current_run or "unknown"
    n_products = len(table_rows)
    n_stores = len(stores_seen)
    has_history = prev_run is not None

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grocery Prices</title>
<style>
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0;
  background: #0f172a; color: #e2e8f0; padding: 16px; }}
h1 {{ font-size: 1.3rem; margin: 0 0 4px; }}
.meta {{ color: #94a3b8; font-size: .85rem; margin-bottom: 14px; }}
table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #1e293b;
  vertical-align: top; }}
th {{ color: #94a3b8; font-weight: 600; cursor: pointer; user-select: none;
  position: sticky; top: 0; background: #0f172a; }}
th:hover {{ color: #e2e8f0; }}
td.prod {{ font-weight: 600; white-space: nowrap; }}
td.prod .thumb {{ width: 34px; height: 34px; object-fit: cover; border-radius: 6px;
  vertical-align: middle; margin-right: 8px; background: #1e293b; }}
td.prod .pname {{ vertical-align: middle; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.store {{ width: 40px; }}
.logo {{ width: 22px; height: 22px; vertical-align: middle; }}
.drange {{ color: #cbd5e1; white-space: nowrap; font-variant-numeric: tabular-nums; }}
.spark {{ width: 130px; }}
.dim {{ color: #64748b; font-size: .8rem; }}
.legend {{ display: flex; gap: 14px; margin: 6px 0 14px; font-size: .82rem; color: #94a3b8; }}
.legend span {{ display: inline-flex; align-items: center; gap: 5px; }}
.banner {{ background: #1e293b; border-radius: 8px; padding: 10px 14px;
  margin-bottom: 14px; font-size: .9rem; }}
.banner b {{ color: #4ade80; }}
</style></head>
<body>
<h1>🪸 Grocery Prices</h1>
<div class="meta">Last run: {esc(last_run)} &middot; {n_products} price rows &middot;
 {n_stores} stores</div>
<div class="legend">
  <span>{STORE_LOGO['Lidl']} Lidl</span>
  <span>{STORE_LOGO['Tesco']} Tesco</span>
  <span>{STORE_LOGO['Albert']} Albert</span>
</div>
<div class="banner">{'Prices update daily. Trend lines need 2+ runs to show movement.'
 if not has_history else 'Trend lines show price movement across runs.'}</div>
<table id="t">
<thead><tr>
<th data-k="prod">Product</th>
<th data-k="store">Store</th>
<th data-k="price" class="num">Price /kg|ks</th>
<th data-k="drange">Discount dates</th>
<th data-k="spark">Trend</th>
</tr></thead>
<tbody>
{''.join(table_rows)}
</tbody></table>
<script>
const t = document.getElementById('t');
const tb = t.tBodies[0];
const rows = [...tb.rows];
document.querySelectorAll('th').forEach(th => {{
  th.onclick = () => {{
    const k = th.dataset.k;
    const dir = th.dataset.d === '1' ? -1 : 1;
    th.dataset.d = dir;
    rows.sort((a,b) => {{
      let x = a.children[th.cellIndex].textContent.trim();
      let y = b.children[th.cellIndex].textContent.trim();
      if (k==='price') {{
        x = parseFloat(x)||0; y = parseFloat(y)||0;
        return (x-y)*dir;
      }}
      return x.localeCompare(y)*dir;
    }});
    rows.forEach(r => tb.appendChild(r));
  }};
}});
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
