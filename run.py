from datetime import datetime
import os
from pathlib import Path
import sys

from scrapers import albert, billa, lidl, tesco
from merge_discounts import main as merge
from scrapers.common import HISTORY_CSV, read_csv, today_timestamp


def refresh_retailer(scraper) -> None:
    path = scraper.CONFIG.csv_path
    previous = path.stat() if path.exists() else None
    # Match the second precision and timezone used by scraper timestamps.
    started_at = datetime.fromisoformat(today_timestamp())
    scraper.main()
    if not path.exists():
        raise RuntimeError("snapshot is missing")
    current = path.stat()
    # Timestamp alone cannot distinguish a failed retry in the same second.
    if previous is not None and (
        current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns
    ) == (
        previous.st_ino, previous.st_size, previous.st_mtime_ns, previous.st_ctime_ns
    ):
        raise RuntimeError("snapshot was not rewritten")
    rows = read_csv(path)
    if not rows:
        raise RuntimeError("snapshot is empty")
    finished_at = datetime.fromisoformat(today_timestamp())
    for row in rows:
        try:
            scraped_at = datetime.fromisoformat(row.get("scraped_at", ""))
            fresh = scraped_at.tzinfo is not None and started_at <= scraped_at <= finished_at
        except (TypeError, ValueError):
            fresh = False
        if not fresh:
            raise RuntimeError("stale or invalid scraped_at")


def main() -> None:
    failures = []
    refreshed = 0
    for scraper in (albert, billa, lidl, tesco):
        path = scraper.CONFIG.csv_path
        originals = {
            target: target.read_bytes() if target.exists() else None
            for target in (path, HISTORY_CSV)
        }
        try:
            refresh_retailer(scraper)
        except Exception as error:
            # A scraper may write a partial/invalid snapshot before failing.
            for target, original in originals.items():
                if original is None:
                    target.unlink(missing_ok=True)
                elif not target.exists() or target.read_bytes() != original:
                    target.write_bytes(original)
            message = f"{scraper.CONFIG.store} refresh failed: {error}"
            failures.append(message)
            print(message, file=sys.stderr, flush=True)
        else:
            refreshed += 1
            print(f"{scraper.CONFIG.store} refreshed successfully", flush=True)
    if refreshed:
        merge()
        # Only publish if merging completed; scraper failure must remain visible.
        if output := os.environ.get("GITHUB_OUTPUT"):
            with Path(output).open("a") as stream:
                stream.write("merged=true\n")
    if failures:
        raise RuntimeError("; ".join(failures))


if __name__ == "__main__":
    main()
