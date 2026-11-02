# Direct BILLA source investigation (2026-11-02)

**Status: discovery works; an offer parser is NOT validated. Daily refresh is
intentionally disabled, not recovered. No CSV was generated or changed.**

## Live authoritative sources

- `https://www.billa.cz/letaky-billa`: HTTP 200, current large leaflet shown as
  29 October–3 November 2026; next leaflet shown as 4–10 November 2026.
- `official-download.html` is the actual download anchor extracted from this
  HTTP response with BeautifulSoup, not a hand-written example. The helper
  `scrapers.billa_pdf.discover_pdf_urls` also returned this exact live URL.
- Current PDF, linked by BILLA (HTTP 200, 27,270,333 bytes, 37 pages):
  `https://view.publitas.com/64069/2709538/pdfs/7215cafc-5676-4f82-b48f-3caa3915184a.pdf`
- Official embedded publication (HTTP 200):
  `https://view.publitas.com/billa-cz/velky-letak-29-10-3-11-2026/`
- `spreads.json` under that publication returns 19 spreads covering pages 1–37.
  Page entries have images, screenshots, unstructured text, background metadata,
  number and ID, not product-price records.
- All 19 actual spread `page/{numbers}/hotspots_data.json` endpoints returned
  HTTP 200. There were zero product hotspots: 17 responses were empty arrays;
  spread 26–27 contained a newsletter external link and spread 34–35 contained
  two generic BILLA external links. `hotspots-evidence.json` preserves these
  actual responses, URLs, status codes and covered page numbers.
- `https://shop.billa.cz/` redirects to
  `https://www.billa.cz/online-nakup`, HTTP 200. Browser and requests agree.
  The page advertises partner delivery through Wolt and foodora, not an
  accessible retailer product catalogue. No partner was substituted as source.

## Real sample offers, manually checked against rendered PDF

These are investigation evidence, NOT parser output or approved CSV rows.

| PDF page | Offer | Price | Validity/condition shown |
|---|---|---|---|
| 1 | Avokádo, 1 ks | 29.90 Kč (previous 49.90 Kč) | 29 October–3 November 2026, regular leaflet period |
| 1, right-hand red panel | Rajčata cherry oválná, 250 g | 29.90 Kč (previous 39.90 Kč) | 29 October–1 November 2026, weekend panel, NOT full leaflet period |
| 1, right-hand red panel | Mandarinka, volná, 1 kg | 24.90 Kč (běžná cena 49.90 Kč) | 29 October–1 November 2026, **BILLA klub badge** |
| 4 | Dýně Hokkaido, 1 kg | 29.90 Kč (previous 39.90 Kč) | ordinary leaflet page; no separate date header shown |
| 4 | Brambory konzumní pozdní, volné, 1 kg | 12.90 Kč (previous 24.90 Kč) | ordinary leaflet page; no separate date header shown |

The first and fourth PDF pages were rendered with PyMuPDF and inspected as
images. PDF text also contains a separate `2. 11. – 3. 11. 2026` section on
page 34 (Hrozny révy vinné bílé bezsemenné, 500 g, 39.90 Kč; Kapusta růžičková,
350 g, 24.90 Kč). Those page-34 associations have NOT been image-validated and
are not used for publication.

## Why the old scaffold cannot be enabled

- Its fixed PDF and October dates were not live discovery.
- `NAŠE CENA` is not present on many real produce discount cards (including
  the pictured avocado and vegetables); using it as an offer gate loses rows.
- Caption/price/name text is split across unrelated blocks and extraction order
  differs from visual card order. Treating price text as a missing name is unsafe.
- Loyalty badges can be graphical: the mandarin card's plain extracted text
  does not preserve the BILLA klub condition shown in its rendered image.
- One page mixes full-period, weekend, and single-day sections. Document-level
  dates or hardcoded page-number mappings are insufficient.
- The PDF repeats some offers later in the document; blind all-page collection
  would duplicate them.

Recovery requires a layout- and condition-aware parser validated on current
and rotated leaflets, including graphical loyalty badges, per-panel validity,
units, duplicate cards, and complete produce coverage. No such parser has been
validated here. Discovery is deliberately separated from daily refresh so
finding a reachable PDF cannot be mistaken for a successful offer scrape.

Both BILLA entry points raise `BillaScrapeError` before networking or writing.
`CONFIG.store` and `CONFIG.csv_path` retain the daily runner interface. Existing
snapshot/history bytes remain untouched and the runner can report failure.
No third-party offer endpoint was accessed in this investigation.
