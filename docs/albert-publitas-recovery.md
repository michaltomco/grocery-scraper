# Albert official leaflet recovery

The app feed remains the preferred structured source. A network/HTTP failure
(not malformed successful app data) enables a conservative official-Publitas
PDF recovery for the default store 583, Praha 5 Zlatý Anděl. No Kupi or other
retailer aggregator is used.

## Production access and discovery

- `https://api.publitas.com/v1/groups/albert/publications.json`
- `https://api.publitas.com/v1/groups/albert/publications/<slug>.json`
- PDF URLs under `https://view.publitas.com/90263/<publication-id>/pdfs/`

These endpoints were fetched from the actual runner network. They do not rely
on Albert's blocked `www.albert.cz`/`letaky.albert.cz` hosts or the unavailable
app DNS. Publication IDs and PDF UUIDs are discovered each execution, not
hardcoded. Only exact generic supermarket weekly slugs are eligible: no
hypermarkets, regional variants, magazines or Christmas catalogs. The publisher
listing includes archives: publication timestamps bound discovery to 14 days
but NEVER determine offer validity. Actual current/upcoming dates come from
the PDF; expired offers are excluded. Requests, PDF size and publication count
are bounded, and empty/invalid results fail before snapshot/history writes.

## Scope and evidence

Albert's official `aktualni-letaky` Next data was inspected on 2026-11-03.
Store 583 was explicitly listed by its configured branch name under generic
supermarket publications 3470643 and 3497124; the sanitized extraction is retained
in `tests/fixtures/albert_official_store_applicability.json` with the raw Next-data
hash. This is not proof of applicability for other stores. Template acceptance
also requires the reviewed publication IDs and exact PDF date ranges, not just
reusable weekly slugs. Explicit
selection of another branch never silently falls back to generic SM offers.
Re-check the official branch mapping if store selection or regional leaflet
structure changes.

The current PDF cover says October 29–November 3, 2026, and explicitly says
October 28 is closed. The website's October 28 metadata must not replace that
purchase-valid start. Current PDF labels say `MIMOŘÁDNĚ BEZ APLIKACE`; never
mark these offers as requiring Můj Albert merely because the old app source
used member-only prices. Conversely do not apply this temporary exception to
future PDFs without evidence.

## Deliberately partial coverage

The recovery parser currently supports exactly five audited cards on page 1 of
`44sm_akcni_letak` and five on page 1 of `45sm_akcni_letak`. It does NOT promise
every leaflet offer and is NOT a generic future-week parser. A newly discovered
publication needs reviewed geometry, real span fixtures and tests before it can
be accepted; otherwise recovery fails closed and retains the previous snapshot.
Only supported, unambiguous PDF layout regions may produce rows. It must retain
quantity, exact sale price, purchase limits, deposits and explicit conditions;
reference/30-day prices and bullet-line regular prices are not sale prices.
Unsupported layouts must not be guessed into offers. Inspect parser tests and
module documentation for the precise supported regions. The collection log
explicitly reports partial leaflet coverage. This is a source recovery, not
an automatic nutrition approval.

All production rows link to the official source with a PDF page anchor. Exact
product pages expose a separate evidence panel with the source, retailer-stated
unit price, purchase conditions and partial-coverage notice; the selling-price
cell remains the unadorned retailer number. Stated unit prices are preserved
even when their rounding differs from pack-price division (e.g. the printed
2.50 Kč/egg for an 18-pack priced 44.90 Kč). A
parser/source failure preserves the previous Albert snapshot and history;
independent retailer refresh behavior is unchanged.
