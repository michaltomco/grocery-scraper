# Grocery refresh runner

Kupi returned Cloudflare HTTP 403 challenge pages from GitHub-hosted Ubuntu runners, including when tested with a Chrome-impersonating HTTP transport. The same public URLs returned live discount HTML from the dedicated host. Refresh jobs therefore use the `grocery-scraper` self-hosted label; tests and Pages deployment remain GitHub-hosted.

## Operations

The dedicated runner is named `grocery-arch`. It is installed at `/home/arch/.local/share/grocery-actions-runner` and managed by the user service `/home/arch/.config/systemd/user/grocery-actions-runner.service`.

Commands on this host:

```
systemctl --user status grocery-actions-runner
journalctl --user -u grocery-actions-runner -n 50 --no-pager
systemctl --user restart grocery-actions-runner
systemctl --user stop grocery-actions-runner
loginctl show-user arch -p Linger
```

Linger must be enabled for unattended operation after logout and at boot. The host needs power, Internet connectivity, and a correct system clock. The runner uses its own `_work` checkout, not the interactive project working tree. Runner auto-updates are enabled.

## Security

This is a public repository. Never route pull-request or fork code to this runner. The refresh workflow accepts only main-branch schedule/manual events, with an explicit main-ref job guard. Keep tests and deploy jobs GitHub-hosted. Only trusted administrators should be able to modify main and workflows: runner jobs execute as the local `arch` user and can access that user's files. The runner label is routing, not a security boundary.

## Daily sources

`run.py` uses direct retailer sources: Lidl.cz for produce, the Albert app feed, and Tesco Online. Albert network outages can use [official Publitas PDF recovery](albert-publitas-recovery.md), restricted to store 583 and five audited cover cards from each of publications `44sm_akcni_letak` and `45sm_akcni_letak`. This is partial recovery, not full leaflet coverage; new publications require parser review. Billa is deliberately disabled until its direct offer parser is validated, preserving its previous snapshot. None of the daily sources falls back to Kupi. Scheduled and manual refreshes use the same entry point.

## Refresh and publication

The hourly schedule checks for the 06:00 Europe/Prague window, including daylight saving time. Manual dispatch bypasses the time check. Python output is unbuffered and the refresh job has a 45-minute timeout.

A category/page fetch error must fail the scrape, not silently truncate a store snapshot. The documented Albert PDF recovery is an explicitly bounded exception: only reviewed cover cards are supported and coverage is reported as partial. Previously saved snapshots are retained on source/parser failure. The orchestrator rejects unchanged, empty, stale, or malformed-timestamp snapshots before merging. A failed retailer does not stop later retailers: healthy snapshots can be merged, built and published, while the overall workflow remains failed to expose the source problem.

After a successful changed-data push, the workflow explicitly dispatches `pages.yml` using `GITHUB_TOKEN` with `actions: write`; pushes made with that token do not themselves trigger the Pages push workflow. The deployment job rebuilds from main. No changed data means no deployment dispatch.

The manual `kupi-connectivity.yml` workflow can reproduce hosted-runner connectivity problems without modifying price data. Its expected result is failure while the upstream challenge remains in place.
