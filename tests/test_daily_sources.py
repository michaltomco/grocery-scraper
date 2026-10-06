"""Ensure the daily orchestrator uses Lidl.cz, not a Kupi Lidl fallback."""
from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

import run
from scrapers.common import read_csv, today_timestamp, write_csv
from tests.test_lidl import FIXTURE, cards_html

class DailySourceTests(unittest.TestCase):
    def test_daily_refresh_fetches_lidl_directly_and_preserves_common_schema(self):
        response = Mock(text=cards_html(json.loads(FIXTURE.read_text())))
        with TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            for scraper in (run.albert, run.billa, run.lidl, run.tesco):
                path = root / f'{scraper.CONFIG.store.lower()}.csv'
                stack.enter_context(patch.object(scraper, 'CONFIG', replace(scraper.CONFIG, csv_path=path)))
                if scraper is not run.lidl:
                    stack.enter_context(patch.object(scraper, 'main', side_effect=lambda path=path: write_csv(path, [{'scraped_at': today_timestamp()}])))
            fetch = stack.enter_context(patch('scrapers.lidl.requests.get', return_value=response))
            stack.enter_context(patch('scrapers.lidl.append_history'))
            stack.enter_context(patch('scrapers.common.run_kupi_food_scraper', side_effect=AssertionError('Lidl must not use Kupi')))
            merge = stack.enter_context(patch('run.merge'))
            run.main()
            fetch.assert_called_once()
            self.assertEqual(urlparse(fetch.call_args.args[0]).hostname, 'www.lidl.cz')
            rows = read_csv(root / 'lidl.csv')
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(row['store'] == 'Lidl' and urlparse(row['url']).hostname == 'www.lidl.cz' for row in rows))
            merge.assert_called_once_with()

if __name__ == '__main__':
    unittest.main()
