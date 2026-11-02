"""Daily Prague refresh gate: delayed schedules can catch up after 06:00."""
import argparse
import csv
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PRAGUE = ZoneInfo("Europe/Prague")
SNAPSHOTS = ("albert.csv", "billa.csv", "lidl.csv", "tesco.csv")


def refresh_due(now: datetime, event: str, root: Path = Path(".")) -> bool:
    if event == "workflow_dispatch":
        return True
    local_now = now.astimezone(PRAGUE)
    if local_now.hour < 6:
        return False
    for name in SNAPSHOTS:
        try:
            with (root / name).open(newline="") as file:
                rows = list(csv.DictReader(file))
            if not rows:
                return True
            for row in rows:
                stamp = datetime.fromisoformat(row.get("scraped_at", ""))
                if (stamp.tzinfo is None or stamp > now
                        or stamp.astimezone(PRAGUE).date() != local_now.date()):
                    return True
        except (OSError, ValueError, TypeError):
            return True
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--now", type=datetime.fromisoformat,
                        default=datetime.now(PRAGUE))
    args = parser.parse_args()
    due = refresh_due(args.now, args.event)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        local_now = args.now.astimezone(PRAGUE)
        reason = ("manual override" if args.event == "workflow_dispatch" else
                  "before 06:00 Europe/Prague" if local_now.hour < 6 else
                  "one or more snapshots missing, invalid or not refreshed today" if due else
                  "all four snapshots already refreshed today")
        with Path(summary).open("a") as stream:
            stream.write(f"## Refresh gate\n{local_now.isoformat()} — {reason}.\n\n")
            if due:
                stream.write("Proceeding to retailer attempts (not a refresh success).\n")
            else:
                stream.write("0 refreshed · 0 failed · 4 skipped. No download, merge or data-date change.\n\n")
                for store in ("Albert", "Billa", "Lidl", "Tesco"):
                    stream.write(f"- {store}: skipped — {reason}\n")
    print(f"run={str(due).lower()}")
