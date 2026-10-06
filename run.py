from datetime import datetime

from scrapers import albert, billa, lidl, tesco
from merge_discounts import main as merge
from scrapers.common import read_csv, today_timestamp


def main() -> None:
    for scraper in (albert, billa, lidl, tesco):
        path = scraper.CONFIG.csv_path
        previous = path.stat() if path.exists() else None
        # Match the second precision and timezone used by scraper timestamps.
        started_at = datetime.fromisoformat(today_timestamp())
        scraper.main()
        if not path.exists():
            raise RuntimeError(f"{scraper.CONFIG.store} refresh failed: snapshot is missing")
        current = path.stat()
        # Use nanoseconds explicitly; stat_result equality can lose precision.
        if previous is not None and (
            current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns
        ) == (
            previous.st_ino, previous.st_size, previous.st_mtime_ns, previous.st_ctime_ns
        ):
            raise RuntimeError(f"{scraper.CONFIG.store} refresh failed: snapshot was not rewritten")
        rows = read_csv(path)
        if not rows:
            raise RuntimeError(f"{scraper.CONFIG.store} refresh failed: snapshot is empty")
        finished_at = datetime.fromisoformat(today_timestamp())
        for row in rows:
            try:
                scraped_at = datetime.fromisoformat(row.get("scraped_at", ""))
                fresh = scraped_at.tzinfo is not None and started_at <= scraped_at <= finished_at
            except (TypeError, ValueError):
                fresh = False
            if not fresh:
                raise RuntimeError(f"{scraper.CONFIG.store} refresh failed: stale or invalid scraped_at")
    merge()


if __name__ == "__main__":
    main()
