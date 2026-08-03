"""Generate a self-contained HTML dashboard from the scraped grocery data.

Reads history.csv (cumulative) and produces index.html:
  - summary header (last run, # items, # stores)
  - sortable table: each product, all stores with normalized /kg or /ks price,
    best store highlighted green, worst red, a mini price-bar per store,
    and a price-trend sparkline (populates as more daily runs accumulate)
No third-party deps; pure stdlib. Output is a single file with inline CSS/JS.
"""
import csv
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
INDEX_HTML = SITE_DIR / "index.html"


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

    # current run = latest scraped_at across all rows
    timestamps = sorted({r.get("scraped_at", "") for r in rows}, reverse=True)
    current_ts = timestamps[0] if timestamps else ""
    prev_ts = timestamps[1] if len(timestamps) > 1 else None

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
        if r.get("scraped_at", "") == current_ts:
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
                }

    products: dict[str, list[dict]] = defaultdict(list)
    for (product, store), e in current_entries.items():
        products[product].append(e)

    # rank colors: best=green, worst=red, middle=neutral
    table_rows = []
    for product in sorted(products):
        entries = products[product]
        if not entries:
            continue
        vals = [e["val"] for e in entries if e["val"] is not None]
        if not vals:
            continue
        best = min(vals)
        worst = max(vals)
        cells = []
        multi = len(vals) > 1
        for e in sorted(entries, key=lambda x: (x["val"] is None, x["val"] or 0)):
            v = e["val"]
            if v is None:
                continue
            if not multi:
                color = "#64748b"  # neutral: only one store carries it
            elif v == best:
                color = "#16a34a"
            elif v == worst:
                color = "#dc2626"
            else:
                color = "#d97706"
            bar_w = 100  # full width base; we show price as text + colored chip
            cells.append(
                f'<div class="storecell" style="border-left:4px solid {color}">'
                f'<span class="st">{esc(e["store"])}</span> '
                f'<span class="pr">{v:.2f}/{e["unit"]}</span></div>'
            )
        spread = worst - best
        save = spread  # vs worst; but "save vs 2nd" is more useful
        sorted_vals = sorted(vals)
        save_vs_2nd = sorted_vals[1] - sorted_vals[0] if len(sorted_vals) > 1 else 0.0
        # combined sparkline: overlay all stores' series for this product
        combo = []
        for (p, s), ser in series.items():
            if p == product:
                combo.extend(ser)
        spark = sparkline(combo) if len(combo) >= 1 else ""
        n_stores = len(entries)
        table_rows.append(
            f"<tr>"
            f'<td class="prod">{esc(product)}</td>'
            f'<td class="stores">{"".join(cells)}</td>'
            f'<td class="num">{best:.2f}</td>'
            f'<td class="num">{save_vs_2nd:.2f}</td>'
            f'<td class="num">{n_stores}</td>'
            f'<td class="spark">{spark or "<span class=dim>1 run</span>"}</td>'
            f"</tr>"
        )

    last_run = current_ts or "unknown"
    n_products = len(table_rows)
    n_stores = len(stores_seen)
    has_history = prev_ts is not None

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
td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.stores {{ min-width: 220px; }}
.storecell {{ padding: 2px 0; }}
.storecell .st {{ display: inline-block; width: 64px; color: #cbd5e1; }}
.storecell .pr {{ font-variant-numeric: tabular-nums; }}
.spark {{ width: 130px; }}
.dim {{ color: #64748b; font-size: .8rem; }}
.banner {{ background: #1e293b; border-radius: 8px; padding: 10px 14px;
  margin-bottom: 14px; font-size: .9rem; }}
.banner b {{ color: #4ade80; }}
</style></head>
<body>
<h1>🪸 Grocery Prices</h1>
<div class="meta">Last run: {esc(last_run)} &middot; {n_products} items &middot;
 {n_stores} stores (Albert / Lidl / Tesco)</div>
<div class="banner">{'Prices update daily. Trend lines need 2+ runs to show movement.'
 if not has_history else 'Trend lines show price movement across runs.'}</div>
<table id="t">
<thead><tr>
<th data-k="prod">Product</th>
<th data-k="stores">Stores (best = green)</th>
<th data-k="best" class="num">Best /kg|ks</th>
<th data-k="save" class="num">Save vs 2nd</th>
<th data-k="n" class="num">#Stores</th>
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
      if (k==='best'||k==='save'||k==='n') {{
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
