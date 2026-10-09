"""Exercise the workflow's actual gate with a delayed GitHub schedule."""
import csv
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RefreshScheduleTests(unittest.TestCase):
    def gate(self, now, dates, event="schedule"):
        workflow = (ROOT / ".github/workflows/refresh-data.yml").read_text()
        match = re.search(r"name: Check Prague scrape time\n(?:.*\n)*?        run: \|\n((?:          .*\n)+)", workflow)
        self.assertIsNotNone(match, "Workflow must have a daily refresh gate")
        assert match is not None
        block = match.group(1)
        script = "\n".join(line[10:] for line in block.splitlines())
        script = script.replace("${{ github.event_name }}", event)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name, date in dates.items():
                with (path / f"{name}.csv").open("w") as f:
                    writer = csv.DictWriter(f, fieldnames=["scraped_at"])
                    writer.writeheader()
                    stamps = date if isinstance(date, list) else [date]
                    for stamp in stamps:
                        writer.writerow({"scraped_at": stamp})
            if (ROOT / "refresh_schedule.py").exists():
                shutil.copy(ROOT / "refresh_schedule.py", path)
            bindir = path / "bin"
            bindir.mkdir()
            date_command = bindir / "date"
            date_command.write_text('#!/bin/sh\ncase "$*" in *%H*) printf "%s\\n" "$TEST_HOUR";; *) printf "%s\\n" "$TEST_NOW";; esac\n')
            date_command.chmod(0o755)
            output = path / "output"
            env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
                       TEST_HOUR=now[11:13], TEST_NOW=now,
                       GITHUB_EVENT_NAME=event, GITHUB_OUTPUT=str(output))
            subprocess.run(["bash", "-e", "-c", script], cwd=path, env=env,
                           check=True, capture_output=True, text=True)
            return "run=true" in output.read_text()

    def snapshots(self, date="2026-10-07T05:47:00+02:00") -> dict[str, str | list[str]]:
        return dict.fromkeys(["albert", "billa", "lidl", "tesco"], date)

    def test_delayed_schedule_catches_up_after_six(self):
        self.assertTrue(self.gate("2026-10-09T08:53:00+02:00", self.snapshots()),
                        "Delayed scheduled jobs must download stale data after 06:00 Prague")


    def test_no_automatic_download_before_six(self):
        self.assertFalse(self.gate("2026-10-09T05:59:00+02:00", self.snapshots()))

    def test_download_at_six(self):
        self.assertTrue(self.gate("2026-10-09T06:00:00+02:00", self.snapshots()))

    def test_no_duplicate_when_all_snapshots_are_fresh(self):
        self.assertFalse(self.gate("2026-10-09T14:00:00+02:00",
                                   self.snapshots("2026-10-09T08:54:00+02:00")))

    def test_one_stale_retailer_triggers_retry(self):
        dates = self.snapshots("2026-10-09T08:54:00+02:00")
        dates["billa"] = "2026-10-07T05:47:00+02:00"
        self.assertTrue(self.gate("2026-10-09T14:00:00+02:00", dates))

    def test_missing_snapshot_triggers_retry(self):
        dates = self.snapshots("2026-10-09T08:54:00+02:00")
        del dates["lidl"]
        self.assertTrue(self.gate("2026-10-09T14:00:00+02:00", dates))

    def test_invalid_and_future_timestamps_trigger_retry(self):
        for stamp in ("", "bad", "2026-10-09T08:54:00", "2026-10-10T08:54:00+02:00"):
            with self.subTest(stamp=stamp):
                self.assertTrue(self.gate("2026-10-09T14:00:00+02:00", self.snapshots(stamp)))

    def test_empty_or_partly_stale_snapshot_triggers_retry(self):
        for stamps in ([], ["2026-10-09T08:54:00+02:00", "2026-10-07T08:54:00+02:00"]):
            with self.subTest(stamps=stamps):
                dates = self.snapshots("2026-10-09T08:54:00+02:00")
                dates["albert"] = stamps
                self.assertTrue(self.gate("2026-10-09T14:00:00+02:00", dates))

    def test_manual_download_overrides_time_and_freshness(self):
        self.assertTrue(self.gate("2026-10-09T02:00:00+02:00",
                                  self.snapshots("2026-10-09T01:00:00+02:00"),
                                  "workflow_dispatch"))

    def test_prague_dst_conversion(self):
        for now, expected in (("2026-10-25T04:59:00+00:00", False),
                              ("2026-10-25T05:00:00+00:00", True),
                              ("2026-03-29T03:59:00+00:00", False),
                              ("2026-03-29T04:00:00+00:00", True)):
            with self.subTest(now=now):
                self.assertEqual(self.gate(now, self.snapshots("2026-03-01T08:00:00+01:00")), expected)


if __name__ == "__main__":
    unittest.main()
