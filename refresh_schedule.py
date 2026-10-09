"""Daily Prague refresh gate: delayed schedules can catch up after 06:00."""
import argparse
import csv
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
    print(f"run={str(due).lower()}")
