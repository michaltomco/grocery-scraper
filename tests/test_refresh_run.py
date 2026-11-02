"""Offline regression tests for fail-visible refresh orchestration."""

from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import run
from scrapers.common import today_timestamp, write_csv


class RefreshRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.stack.enter_context(patch("run.HISTORY_CSV", self.root / "history.csv"))
        self.scrapers = (run.albert, run.billa, run.lidl, run.tesco)
        self.paths = []
        self.calls = []
        for scraper in self.scrapers:
            path = self.root / f"{scraper.CONFIG.store.lower()}.csv"
            self.paths.append(path)
            self.stack.enter_context(patch.object(scraper, "CONFIG", replace(scraper.CONFIG, csv_path=path)))
            self.stack.enter_context(patch.object(scraper, "main", side_effect=self.writer(scraper)))
        self.merge = self.stack.enter_context(patch("run.merge"))

    def writer(self, scraper):
        def scrape():
            self.calls.append(scraper.CONFIG.store)
            write_csv(scraper.CONFIG.csv_path, [{"store": scraper.CONFIG.store, "scraped_at": today_timestamp()}])
        return scrape

    def test_fresh_snapshots_from_all_four_stores_are_merged(self) -> None:
        run.main()
        self.assertEqual(self.calls, [scraper.CONFIG.store for scraper in self.scrapers])
        self.merge.assert_called_once_with()

    def test_failed_retailer_does_not_block_later_retailers(self) -> None:
        write_csv(self.paths[0], [{"store": "Albert", "scraped_at": "2026-10-11T00:00:00+02:00"}])
        original = self.paths[0].read_bytes()

        def fail_after_write():
            self.paths[0].write_text("partial output")
            raise ConnectionError("Albert unavailable")

        with patch.object(run.albert, "main", side_effect=fail_after_write):
            with self.assertRaisesRegex(Exception, "Albert.*unavailable") as raised:
                run.main()
        self.assertEqual(self.calls, ["Billa", "Lidl", "Tesco"])
        self.assertIsInstance(raised.exception, RuntimeError)
        self.assertEqual(self.paths[0].read_bytes(), original)
        self.merge.assert_called_once_with()

    def test_multiple_failures_are_reported_after_all_retailers_run(self) -> None:
        with patch.object(run.albert, "main", side_effect=ConnectionError("offline")), patch.object(run.lidl, "main", side_effect=ValueError("bad data")):
            with self.assertRaisesRegex(RuntimeError, "Albert.*offline.*Lidl.*bad data"):
                run.main()
        self.assertEqual(self.calls, ["Billa", "Tesco"])
        self.merge.assert_called_once_with()
        self.assertFalse(self.paths[0].exists())
        self.assertFalse(self.paths[2].exists())

    def test_all_retailers_failing_does_not_merge(self) -> None:
        for scraper in self.scrapers:
            scraper.main.side_effect = ConnectionError("offline")
        with self.assertRaisesRegex(RuntimeError, "Albert.*Billa.*Lidl.*Tesco"):
            run.main()
        for scraper in self.scrapers:
            scraper.main.assert_called_once_with()
        self.merge.assert_not_called()

    def test_partial_success_signals_merge_to_workflow(self) -> None:
        output = self.root / "github-output"
        with patch.dict("os.environ", {"GITHUB_OUTPUT": str(output)}), patch.object(run.albert, "main", side_effect=ConnectionError("offline")):
            with self.assertRaises(RuntimeError):
                run.main()
        self.assertEqual(output.read_text(), "merged=true\n")

    def test_merge_failure_does_not_signal_publishable_data(self) -> None:
        output = self.root / "github-output"
        self.merge.side_effect = OSError("merge failed")
        with patch.dict("os.environ", {"GITHUB_OUTPUT": str(output)}):
            with self.assertRaisesRegex(OSError, "merge failed"):
                run.main()
        self.assertFalse(output.exists())

    def test_failed_retailer_restores_history(self) -> None:
        history = self.root / "history.csv"
        history.write_text("original history")

        def fail_after_history_write():
            history.write_text("partial history")
            raise ConnectionError("offline")

        with patch("run.HISTORY_CSV", history, create=True), patch.object(run.albert, "main", side_effect=fail_after_history_write):
            with self.assertRaises(RuntimeError):
                run.main()
        self.assertEqual(history.read_text(), "original history")

    def test_empty_snapshot_is_rejected_without_blocking_other_stores(self) -> None:
        with patch.object(run.albert, "main", side_effect=lambda: write_csv(self.paths[0], [])):
            with self.assertRaisesRegex(RuntimeError, "Albert.*empty"):
                run.main()
        self.merge.assert_called_once_with()
        self.merge.reset_mock()

    def test_rewritten_snapshot_requires_every_timestamp_to_be_fresh(self) -> None:
        earlier_today = (datetime.fromisoformat(today_timestamp()) - timedelta(seconds=60)).isoformat()
        for timestamp in (earlier_today, "2000-01-01T00:00:00+00:00", "", "invalid", "2099-01-01T00:00:00+00:00", "2000-01-01T00:00:00"):
            with self.subTest(timestamp=timestamp):
                def scrape():
                    write_csv(self.paths[0], [{"scraped_at": today_timestamp()}, {"scraped_at": timestamp}])
                with patch.object(run.albert, "main", side_effect=scrape):
                    with self.assertRaisesRegex(RuntimeError, "Albert.*scraped_at"):
                        run.main()
                self.merge.assert_called_once_with()
                self.merge.reset_mock()

    def test_unchanged_same_second_snapshot_is_rejected(self) -> None:
        # Timestamp alone cannot distinguish a failed retry in the same second.
        write_csv(self.paths[0], [{"scraped_at": today_timestamp()}])
        original = self.paths[0].read_bytes()
        with patch.object(run.albert, "main", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Albert"):
                run.main()
        self.merge.assert_called_once_with()
        self.merge.reset_mock()
        self.assertEqual(self.paths[0].read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
