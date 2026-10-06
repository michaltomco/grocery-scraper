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

    def test_empty_snapshot_prevents_merge(self) -> None:
        with patch.object(run.albert, "main", side_effect=lambda: write_csv(self.paths[0], [])):
            with self.assertRaisesRegex(RuntimeError, "Albert.*empty"):
                run.main()
        self.merge.assert_not_called()

    def test_rewritten_snapshot_requires_every_timestamp_to_be_fresh(self) -> None:
        earlier_today = (datetime.fromisoformat(today_timestamp()) - timedelta(seconds=60)).isoformat()
        for timestamp in (earlier_today, "2000-01-01T00:00:00+00:00", "", "invalid", "2099-01-01T00:00:00+00:00", "2000-01-01T00:00:00"):
            with self.subTest(timestamp=timestamp):
                def scrape():
                    write_csv(self.paths[0], [{"scraped_at": today_timestamp()}, {"scraped_at": timestamp}])
                with patch.object(run.albert, "main", side_effect=scrape):
                    with self.assertRaisesRegex(RuntimeError, "Albert.*scraped_at"):
                        run.main()
                self.merge.assert_not_called()

    def test_unchanged_same_second_snapshot_prevents_merge(self) -> None:
        # Timestamp alone cannot distinguish a failed retry in the same second.
        write_csv(self.paths[0], [{"scraped_at": today_timestamp()}])
        original = self.paths[0].read_bytes()
        with patch.object(run.albert, "main", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Albert"):
                run.main()
        self.merge.assert_not_called()
        self.assertEqual(self.paths[0].read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
