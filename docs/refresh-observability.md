# Refresh visibility and publication safety

## Three different clocks

- **Snapshot data time** is each retailer CSV's actual timezone-aware `scraped_at`.
  The dashboard shows the oldest and newest row timestamps per retailer. It reads
  the CSVs even on legacy checkouts without `refresh_status.json`; missing or invalid
  timestamps are unknown, never replaced with the current build time.
- **Refresh attempt time** is when `run.py` tried the retailer, whether or not it
  succeeded. A failed attempt retains the previous snapshot and its original dates.
- **Site build time** describes the Pages deployment build, **not a data refresh**.
  A status-only deployment can therefore be new while every snapshot remains old.

Every dashboard, grouped product page and exact product page displays this
freshness information. A retailer's result describes the last recorded attempt;
its data dates independently describe the committed snapshot served by the site.
This distinction also matters if a successful scrape was withheld after a failed
merge or dashboard build.

The timeline and offer selection remain explicitly **anchored to the build day**.
Changing only the JavaScript date would misrepresent the server-filtered offer
set and pre-rendered date squares. Browser code compares that anchor with today's
**Europe/Prague** date, warns when they differ, and rechecks on tab visibility
changes and once per minute. Refreshing the browser never claims to refresh data.
The header counts retailers in the displayed active-window offer set, not every
retailer ever seen in history. User category/store filters do not change this
build-wide count.

## Machine-readable status

`run.py` atomically writes root-level `refresh_status.json` after all retailer
attempts, including when every retailer fails or the final merge raises. The
refresh workflow commits this file and retains it as the `refresh-status` GitHub
artifact. No status file is seeded with made-up success data; the first actual
orchestrated refresh creates it.

Schema version 1:

- `schema_version`: `1`
- `started_at`, `finished_at`: timezone-aware orchestration timestamps
- `retailers`: object keyed by `Albert`, `Billa`, `Lidl`, `Tesco`
  - `status`: `refreshed` or `failed`
  - `started_at`, `finished_at`: attempt timestamps
  - `error`: exception type/message, or `null`
  - `rows`: retained/successful snapshot row count, or `null` if unreadable
  - `scraped_at`: newest real snapshot observation, or `null`
  - `oldest_scraped_at`: oldest real snapshot observation, or `null`
- `merge`: `status` (`merged`, `failed`, `skipped`) and nullable `error`

An old snapshot preserved after failure keeps its old dates in this file. A
retailer refresh requires a rewritten, nonempty snapshot in which **every**
`scraped_at` value falls within the attempt's bounds.
No source fallback is added by this observability layer.

## GitHub summaries and commit gates

The Prague schedule gate always explains its decision. A green no-op reports
`0 refreshed · 0 failed · 4 skipped`, names each skipped retailer and explains
whether the job was before 06:00 or all snapshots were already dated today. It
preserves the last actual refresh status rather than overwriting it as a success.
A manual dispatch bypasses the schedule gate.

A completed orchestration summary includes refreshed/failed counts, each
retailer's result/error and real snapshot date range, and the merge result.

Publication follows these rules:

| Result | Commit | Deploy | Overall refresh job |
|---|---|---|---|
| All sources succeed; merge/build succeed | Status + refreshed data | Yes, if committed changes | Success |
| Some sources fail; merge/build succeed | Status + healthy updates + retained failed snapshots | Yes | **Failure** |
| All sources fail | **Status only** | Safe previously committed snapshots + new failure status | **Failure** |
| Merge fails | **Status only**, never partial merged files | Safe previously committed snapshots + failure status | **Failure** |
| Dashboard build fails | **Status only**, never scrape/build output | Status-only dispatch uses previous committed data | **Failure** |
| Gate skips | Nothing | No | Success, explicitly skipped |

`merged=true` is emitted only after a completed merge. The workflow additionally
requires a successful dashboard build before staging CSV/history/image/cache
updates. Status upload and commit use `always()` with an attempted-scrape check;
Pages dispatch also runs after an earlier failure if the status commit succeeded.
The final step tests the scraper's **outcome**, not its tolerated conclusion, so
`continue-on-error` permits publication without concealing an outage. Failed
commits, uploads, builds or deployments remain GitHub failures as usual.

## Offline verification

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
```

Coverage includes all-failed preservation, merge-failed status without a publish
signal, a real subprocess/CSV merge with one failed retailer and a nonzero exit,
execution of the workflow staging script under success/failure conditions,
legacy CSV-only freshness, and real Chromium tests. Browser fixtures fix their
build day and advance the browser clock three days to verify stale warnings on
all three page types, with no console errors or uncaught JavaScript exceptions.

No generated CSVs, status snapshots, or site assets are part of the code change.
Live source recovery and actual Pages deployment must be verified separately;
passing offline fixtures does not establish that any retailer is currently up.
