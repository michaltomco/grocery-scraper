from datetime import datetime
import os
import csv
import json
from html import escape
from pathlib import Path
import sys

from scrapers import albert, billa, lidl, tesco
from merge_discounts import main as merge
from scrapers.common import HISTORY_CSV, read_csv, today_timestamp


STATUS_PATH = Path(__file__).resolve().parent / "refresh_status.json"


def snapshot_status(path):
    """Report observed timestamps, never the time we attempted a download."""
    try:
        rows = read_csv(path) if path.exists() else []
    except (OSError, ValueError, csv.Error):
        return {"rows": None, "scraped_at": None, "oldest_scraped_at": None}
    stamps = []
    for row in rows:
        try:
            stamp = datetime.fromisoformat(row.get("scraped_at", ""))
            if stamp.tzinfo is None:
                raise ValueError("naive timestamp")
            stamps.append(stamp)
        except (ValueError, TypeError):
            return {"rows": len(rows), "scraped_at": None, "oldest_scraped_at": None}
    return {"rows": len(rows),
            "scraped_at": max(stamps).isoformat() if stamps else None,
            "oldest_scraped_at": min(stamps).isoformat() if stamps else None}


def save_status(status):
    status["finished_at"] = today_timestamp()
    temporary = STATUS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(STATUS_PATH)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        retailers = status["retailers"]
        refreshed = sum(item["status"] == "refreshed" for item in retailers.values())
        lines = ["## Retailer refresh", f"{refreshed} refreshed · {len(retailers) - refreshed} failed · 0 skipped",
                 "", "| Retailer | Result | Oldest → newest scraped_at | Error |", "|---|---|---|---|"]
        def cell(value):
            return escape(str(value or "unknown")).replace("|", "&#124;").replace("\n", " ")
        for store, item in retailers.items():
            lines.append(f"| {cell(store)} | {cell(item['status'])} | {cell(item['oldest_scraped_at'])} → {cell(item['scraped_at'])} | {cell(item.get('error') or '—')} |")
        lines.append(f"\nMerge: {cell(status['merge']['status'])}. {cell(status['merge'].get('error') or 'Failed snapshots retained; no timestamps relabelled.')}\n")
        with Path(summary).open("a") as stream:
            stream.write("\n".join(lines))


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
    status = {"schema_version": 1, "started_at": today_timestamp(),
              "retailers": {}, "merge": {"status": "skipped", "error": None}}
    for scraper in (albert, billa, lidl, tesco):
        path = scraper.CONFIG.csv_path
        originals = {
            target: target.read_bytes() if target.exists() else None
            for target in (path, HISTORY_CSV)
        }
        attempt = {"started_at": today_timestamp(), "status": "refreshed", "error": None}
        try:
            refresh_retailer(scraper)
        except Exception as error:
            # A scraper may write a partial/invalid snapshot before failing.
            for target, original in originals.items():
                if original is None:
                    target.unlink(missing_ok=True)
                elif not target.exists() or target.read_bytes() != original:
                    target.write_bytes(original)
            attempt.update(status="failed", error=f"{type(error).__name__}: {error}")
            message = f"{scraper.CONFIG.store} refresh failed: {error}"
            failures.append(message)
            print(message, file=sys.stderr, flush=True)
        else:
            refreshed += 1
            print(f"{scraper.CONFIG.store} refreshed successfully", flush=True)
        attempt.update(snapshot_status(path), finished_at=today_timestamp())
        status["retailers"][scraper.CONFIG.store] = attempt
    try:
        if refreshed:
            merge()
            status["merge"]["status"] = "merged"
            # Only publish if merging completed; scraper failure remains visible.
            if output := os.environ.get("GITHUB_OUTPUT"):
                with Path(output).open("a") as stream:
                    stream.write("merged=true\n")
    except Exception as error:
        status["merge"] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
        raise
    finally:
        save_status(status)
    if failures:
        raise RuntimeError("; ".join(failures))


if __name__ == "__main__":
    main()
