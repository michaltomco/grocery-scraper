"""Generate a self-contained HTML dashboard from the scraped grocery data.

Reads history.csv (cumulative) and produces index.html:
  - summary header (last run, # items, # stores)
  - sortable table: one row per product; Store and Price are separate columns
    with each store stacked vertically; clickable legend toggles stores;
    plus a price-trend sparkline (populates as more daily runs accumulate)
No third-party deps; pure stdlib. Output is a single file with inline CSS/JS.
"""
import csv
import re
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
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


def parse_range(date_range: str) -> tuple[str, str]:
    """Return (start_iso, end_iso) for a date_range like '2026-08-04' or
    '2026-08-06 - 2026-08-09'. Missing end defaults to the start."""
    if not date_range:
        return "", ""
    if " - " in date_range:
        start, end = date_range.split(" - ", 1)
        return start.strip(), end.strip()
    return date_range.strip(), date_range.strip()


def fmt_cz(iso: str) -> str:
    """Format an ISO date or an ISO 'start - end' range as Czech D.M.YYYY
    (day first). Falls back to the raw string if not a parseable ISO date."""
    if not iso:
        return ""
    if " - " in iso:
        a, b = iso.split(" - ", 1)
        return f"{fmt_cz(a)} – {fmt_cz(b)}"
    try:
        d = date.fromisoformat(iso.strip())
    except ValueError:
        return iso
    return f"{d.day}.{d.month}.{d.year}"


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
                    "raw_name": r.get("product_name", product),
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
        entries = sorted(
            products[product],
            key=lambda x: (x["val"] is None, x["val"] or 0),
        )
        first = entries[0] if entries else {}
        pretty = pretty_name(first.get("raw_name", product))
        img_path = cache_image(first.get("product_id", product), first.get("image_url", ""))
        img_tag = (
            f'<img class="thumb" src="{img_path}" alt="{esc(pretty)}" loading="lazy">'
            if img_path else '<span class="dim">no img</span>'
        )
        # stacked store / price / date cells, one per store (kept aligned).
        # Each store carries its OWN discount date range; the row-level
        # data-start/data-end is the union (min start, max end) so the date
        # filter shows the row if ANY store's deal falls in the window.
        store_cells = []
        price_cells = []
        date_cells = []
        union_start, union_end = None, None
        for e in entries:
            v = e["val"]
            if v is None:
                continue
            logo = STORE_LOGO.get(e["store"], "")
            color = STORE_COLOR.get(e["store"], "#64748b")
            dr = e.get("date_range", "") or ""
            s, en = parse_range(dr)
            if s and (union_start is None or s < union_start):
                union_start = s
            if en and (union_end is None or en > union_end):
                union_end = en
            price_cells.append(
                f'<div class="pcell" data-store="{esc(e["store"])}" style="border-left:3px solid {color}">'
                f'<span class="pprice">{v:.2f}/{e["unit"]}</span></div>'
            )
            store_cells.append(
                f'<div class="scell" data-store="{esc(e["store"])}">'
                f'{logo}</div>'
            )
            date_cells.append(
                f'<div class="dcell" data-store="{esc(e["store"])}" style="border-left:3px solid {color}">'
                f'{esc(fmt_cz(dr)) or "&mdash;"}</div>'
            )
        table_rows.append(
            f"<tr data-start=\"{esc(union_start or '')}\" data-end=\"{esc(union_end or '')}\">"
            f'<td class="prod">{img_tag}<span class="pname">{esc(pretty)}</span></td>'
            f'<td class="pricelist">{"".join(price_cells)}</td>'
            f'<td class="stores">{"".join(store_cells)}</td>'
            f'<td class="drange">{"".join(date_cells)}</td>'
            f'<td class="spark">{sparkline(series.get((product, entries[0]["store"]), [])) or "<span class=dim>1 run</span>"}</td>'
            f"</tr>"
        )

    last_run = current_run or "unknown"
    n_products = len(table_rows)
    n_stores = len(stores_seen)
    has_history = prev_run is not None

    # Date bounds for the range slider. Far-left = today, far-right = latest
    # discount end date seen. Rows without a parseable date default to today.
    today_iso = date.today().isoformat()
    all_ends = []
    for r in rows:
        s, e = parse_range(r.get("date_range", ""))
        for d in (s, e):
            if d:
                all_ends.append(d)
    date_min = today_iso
    date_max = max([today_iso] + all_ends) if all_ends else today_iso
    # Slider uses integer day offsets: 0 = today, days_span = last discount day.
    days_span = (date.fromisoformat(date_max) - date.fromisoformat(today_iso)).days

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
td.prod {{ font-weight: 600; white-space: nowrap; max-width: 180px; }}
td.prod .thumb {{ width: 40px; height: 40px; object-fit: contain; border-radius: 6px;
  vertical-align: middle; margin-right: 6px; background: #1e293b; }}
td.prod .pname {{ vertical-align: middle; }}
td.pricelist {{ white-space: nowrap; vertical-align: middle; width: 1%; }}
.pcell {{ display: flex; align-items: center; gap: 6px; padding: 1px 0 1px 6px; }}
.pcell .pprice {{ font-variant-numeric: tabular-nums; font-weight: 700; white-space: nowrap;
  font-size: .95rem; }}
td.stores {{ white-space: nowrap; vertical-align: middle; }}
.scell {{ padding: 1px 0; line-height: 0; }}
.scell .logo {{ width: 22px; height: 22px; }}
.drange {{ color: #cbd5e1; white-space: nowrap; font-variant-numeric: tabular-nums;
  vertical-align: middle; }}
.dcell {{ padding: 1px 0 1px 6px; font-size: .82rem; }}
.spark {{ width: 130px; }}
.dim {{ color: #64748b; font-size: .8rem; }}
/* muted = filtered-out store: dimmed for comparison, not removed */
.scell.muted, .pcell.muted {{ opacity: .3; }}
.legend {{ display: flex; gap: 12px; margin: 6px 0 14px; font-size: .82rem; }}
.legend .chip {{ display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  padding: 4px 10px; border-radius: 999px; border: 1px solid #334155;
  background: #1e293b; user-select: none; transition: opacity .15s, background .15s; }}
.legend .chip .logo {{ width: 18px; height: 18px; }}
.legend .chip.off {{ opacity: .35; background: #0f172a; border-color: #1e293b; }}
.legend .chip:hover {{ border-color: #64748b; }}
.controls {{ display: flex; flex-wrap: wrap; align-items: center; gap: 16px;
  margin: 0 0 14px; padding: 10px 14px; background: #1e293b; border-radius: 8px; }}
.toggle {{ cursor: pointer; padding: 6px 14px; border-radius: 999px;
  border: 1px solid #334155; background: #0f172a; color: #e2e8f0; font-size: .85rem;
  user-select: none; transition: background .15s, border-color .15s; }}
.toggle.on {{ background: #4ade80; color: #0f172a; border-color: #4ade80; font-weight: 600; }}
.rangewrap {{ display: flex; flex-direction: column; gap: 4px; min-width: 320px; }}
.rangewrap .rlabels {{ display: flex; justify-content: space-between;
  font-size: .75rem; color: #94a3b8; }}
.dualslider {{ position: relative; height: 30px; }}
.dualslider input[type=range] {{ position: absolute; left: 0; top: 8px; width: 100%;
  margin: 0; pointer-events: none; -webkit-appearance: none; background: none;
  height: 4px; }}
.dualslider input[type=range]::-webkit-slider-thumb {{ -webkit-appearance: none;
  pointer-events: auto; width: 16px; height: 16px; border-radius: 50%;
  background: #60a5fa; border: 2px solid #0f172a; cursor: pointer; }}
.dualslider input[type=range]::-moz-range-thumb {{ pointer-events: auto;
  width: 16px; height: 16px; border-radius: 50%; background: #60a5fa;
  border: 2px solid #0f172a; cursor: pointer; }}
.dualslider .track {{ position: absolute; left: 0; top: 14px; width: 100%;
  height: 4px; background: #334155; border-radius: 2px; }}
.dualslider .fill {{ position: absolute; top: 14px; height: 4px; background: #60a5fa;
  border-radius: 2px; }}
.banner {{ background: #1e293b; border-radius: 8px; padding: 10px 14px;
  margin-bottom: 14px; font-size: .9rem; }}
.banner b {{ color: #4ade80; }}
</style></head>
<body>
<h1>🪸 Grocery Prices</h1>
<div class="meta">Last run: {esc(last_run)} &middot; {n_products} products &middot;
 {n_stores} stores</div>
<div class="legend">
  <span class="chip" data-store="Lidl">{STORE_LOGO['Lidl']} Lidl</span>
  <span class="chip" data-store="Tesco">{STORE_LOGO['Tesco']} Tesco</span>
  <span class="chip" data-store="Albert">{STORE_LOGO['Albert']} Albert</span>
</div>
<div class="controls">
  <span class="toggle" id="todayToggle">Only today</span>
  <div class="rangewrap">
    <div class="dualslider">
      <div class="track"></div>
      <div class="fill" id="rangeFill"></div>
      <input type="range" id="rangeStart" min="0" max="{days_span}" step="1" value="0">
      <input type="range" id="rangeEnd" min="0" max="{days_span}" step="1" value="{days_span}">
    </div>
    <div class="rlabels"><span id="lblStart">{fmt_cz(date_min)}</span><span id="lblEnd">{fmt_cz(date_max)}</span></div>
  </div>
</div>
<div class="banner">{'Prices update daily. Trend lines need 2+ runs to show movement.'
 if not has_history else 'Trend lines show price movement across runs.'}</div>
<table id="t">
<thead><tr>
<th data-k="prod">Product</th>
<th data-k="pricelist">Price</th>
<th data-k="stores">Store</th>
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
      return x.localeCompare(y)*dir;
    }});
    rows.forEach(r => tb.appendChild(r));
  }};
}});

// Combined filters: store mute + "only today" + date-range slider.
const hidden = new Set();
const chips = [...document.querySelectorAll('.legend .chip')];
let onlyToday = false;
// Today as a JS Date; slider works in integer day offsets from today.
const today = new Date("{today_iso}T00:00:00");
function offsetToIso(off) {{
  const d = new Date(today);
  d.setDate(d.getDate() + off);
  return d.toISOString().slice(0, 10);
}}
let rangeStart = offsetToIso(0);
let rangeEnd = offsetToIso({days_span});

function inRange(row) {{
  const s = row.dataset.start || rangeStart;
  const e = row.dataset.end || rangeEnd;
  return s <= rangeEnd && e >= rangeStart;   // overlap test
}}
function isToday(row) {{
  const s = row.dataset.start || rangeStart;
  const e = row.dataset.end || rangeEnd;
  return s <= "{today_iso}" && e >= "{today_iso}";
}}
function applyFilters() {{
  rows.forEach(r => {{
    // store mute (dim, keep for comparison)
    let visibleStores = 0;
    r.querySelectorAll('.scell, .pcell').forEach(c => {{
      const st = c.dataset.store;
      const muted = !!(st && hidden.has(st));
      c.classList.toggle('muted', muted);
      if (!muted) visibleStores++;
    }});
    // date filters (hide whole row)
    let show = inRange(r);
    if (onlyToday) show = show && isToday(r);
    // hide rows with no visible store (every store muted via legend)
    if (show && visibleStores === 0) show = false;
    r.style.display = show ? '' : 'none';
  }});
}}
chips.forEach(chip => {{
  chip.onclick = () => {{
    const s = chip.dataset.store;
    if (hidden.has(s)) {{ hidden.delete(s); chip.classList.remove('off'); }}
    else {{ hidden.add(s); chip.classList.add('off'); }}
    applyFilters();
  }};
}});

// "Only today" toggle
const todayBtn = document.getElementById('todayToggle');
todayBtn.onclick = () => {{
  onlyToday = !onlyToday;
  todayBtn.classList.toggle('on', onlyToday);
  applyFilters();
}};

// Dual-handle date-range slider (far-left = today, far-right = latest end).
const rs = document.getElementById('rangeStart');
const re = document.getElementById('rangeEnd');
const fill = document.getElementById('rangeFill');
const lblS = document.getElementById('lblStart');
const lblE = document.getElementById('lblEnd');
// ISO YYYY-MM-DD -> Czech D.M.YYYY for display only (inputs keep ISO values).
function fmtCz(iso) {{
  const m = /^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/.exec(iso);
  if (!m) return iso;
  return `${{parseInt(m[3],10)}}.${{parseInt(m[2],10)}}.${{m[1]}}`;
}}
function syncSlider() {{
  if (rs.value > re.value) {{            // keep handles from crossing
    if (document.activeElement === rs) re.value = rs.value;
    else rs.value = re.value;
  }}
  const offS = +rs.value, offE = +re.value;
  rangeStart = offsetToIso(offS); rangeEnd = offsetToIso(offE);
  lblS.textContent = fmtCz(rangeStart); lblE.textContent = fmtCz(rangeEnd);
  const span = ({days_span}) || 1;
  const a = offS / span * 100;
  const b = offE / span * 100;
  fill.style.left = a + '%'; fill.style.width = (b - a) + '%';
  applyFilters();
}}
rs.addEventListener('input', syncSlider);
re.addEventListener('input', syncSlider);
syncSlider();
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
