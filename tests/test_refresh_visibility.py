"""Offline contracts for refresh publication safety and visibility."""
import os
from pathlib import Path
import re
import subprocess
import sys
import json
from unittest.mock import patch
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]


def step_script(workflow, name):
    block = workflow.split(f"      - name: {name}\n", 1)[1].split("\n      - ", 1)[0]
    return "\n".join(line[10:] for line in block.split("        run: |\n", 1)[1].splitlines())


class RefreshWorkflowVisibilityTests(unittest.TestCase):
    def test_partial_outage_process_records_truthful_status_and_real_merged_rows(self):
        script = '''
from dataclasses import replace
from pathlib import Path
import os
import run
import merge_discounts
from scrapers.common import read_csv, write_csv, today_timestamp
root = Path(os.environ['FIXTURE_ROOT'])
run.HISTORY_CSV = root / 'history.csv'
run.STATUS_PATH = root / 'refresh_status.json'
merge_discounts.INPUT_CSVS = [root / f'{store}.csv' for store in ('albert', 'billa', 'lidl', 'tesco')]
merge_discounts.OUTPUT_CSV = root / 'all_discounts.csv'
old = '2026-10-11T06:00:00+02:00'
for scraper in (run.albert, run.billa, run.lidl, run.tesco):
    store = scraper.CONFIG.store
    path = root / f'{store.lower()}.csv'
    scraper.CONFIG = replace(scraper.CONFIG, csv_path=path)
    write_csv(path, [{'store': store, 'product_name': store, 'price': '10', 'scraped_at': old}])
    def writer(path=path, store=store):
        if store == 'Albert':
            path.write_text('partial invalid snapshot')
            run.HISTORY_CSV.write_text('partial invalid history')
            raise ConnectionError('offline')
        write_csv(path, [{'store': store, 'product_name': store, 'price': '20', 'scraped_at': today_timestamp()}])
    scraper.main = writer
run.HISTORY_CSV.write_text('original history')
run.main()
'''
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, text=True, capture_output=True,
                env=dict(os.environ, FIXTURE_ROOT=tmp, GITHUB_OUTPUT=str(root / 'output'),
                         GITHUB_STEP_SUMMARY=str(root / 'summary')))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Albert refresh failed: offline', result.stderr)
            status = json.loads((root / 'refresh_status.json').read_text())
            self.assertEqual(status['merge']['status'], 'merged')
            self.assertEqual(status['retailers']['Albert']['status'], 'failed')
            self.assertEqual(status['retailers']['Albert']['scraped_at'], '2026-10-11T06:00:00+02:00')
            self.assertEqual((root / 'output').read_text(), 'merged=true\n')
            self.assertEqual((root / 'history.csv').read_text(), 'original history')
            from scrapers.common import read_csv
            rows = read_csv(root / 'all_discounts.csv')
            self.assertEqual(len(rows), 4)
            self.assertEqual({r['store']: r['price'] for r in rows}, {'Albert': '10', 'Billa': '20', 'Lidl': '20', 'Tesco': '20'})
            self.assertIn('3 refreshed · 1 failed', (root / 'summary').read_text())

    def test_legacy_site_freshness_uses_csv_dates_without_a_status_file(self):
        import build_site
        from scrapers.common import write_csv
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_csv(root / 'albert.csv', [{'scraped_at': '2026-10-11T06:00:00+02:00'},
                                           {'scraped_at': '2026-10-12T06:00:00+02:00'}])
            with patch.object(build_site, 'HISTORY_CSV', root / 'history.csv'), patch.object(build_site, 'REFRESH_STATUS_PATH', root / 'refresh_status.json'):
                panel = build_site.freshness_panel()
            self.assertIn('2026-10-11T06:00:00+02:00 → 2026-10-12T06:00:00+02:00', panel)
            self.assertIn('unknown (no recorded attempt)', panel)
            self.assertNotIn('last attempt: refreshed', panel)

    def test_gate_summary_explains_skipped_retailers_without_relabelling_data(self):
        with TemporaryDirectory() as tmp:
            summary = Path(tmp) / 'summary'
            subprocess.run(['python3', str(ROOT / 'refresh_schedule.py'), '--event', 'schedule',
                            '--now', '2026-11-02T05:59:00+01:00'], cwd=tmp,
                           env=dict(os.environ, GITHUB_STEP_SUMMARY=str(summary)), check=True, capture_output=True)
            self.assertTrue(summary.exists(), 'Even a green no-op must explain the gate decision')
            text = summary.read_text()
            self.assertIn('4 skipped', text)
            self.assertIn('before 06:00', text)
            for store in ('Albert', 'Billa', 'Lidl', 'Tesco'):
                self.assertIn(store, text)
            self.assertFalse((Path(tmp) / 'refresh_status.json').exists(), 'Gate must preserve previous attempt status')

    def test_status_is_committed_even_without_a_safe_merge(self):
        workflow = (ROOT / ".github/workflows/refresh-data.yml").read_text()
        self.assertIn("path: refresh_status.json", workflow, "Retain the status artifact even when commit/push fails")
        commit = workflow.split("      - name: Commit refreshed data\n")[1].split("\n      - ")[0]
        self.assertIn("always()", commit)
        self.assertIn("steps.scrape.outcome", commit)
        script = step_script(workflow, "Commit refreshed data")
        for merged, build, expected in [("", "skipped", False), ("true", "failure", False), ("true", "success", True)]:
            with self.subTest(merged=merged, build=build), TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "refresh_status.json").write_text('{}')
                git = root / "git"
                git.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$LOG"\nif [ "$1" = diff ]; then exit 1; fi\n')
                git.chmod(0o755)
                env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", LOG=str(root / 'log'),
                           GITHUB_OUTPUT=str(root / 'output'), MERGED=merged, BUILD_OUTCOME=build)
                subprocess.run(['bash', '-eu', '-c', script], cwd=root, env=env, check=True, capture_output=True)
                adds = [line for line in (root / 'log').read_text().splitlines() if line.startswith('add ')]
                self.assertIn('add refresh_status.json', adds)
                self.assertEqual(any('all_discounts.csv' in line for line in adds), expected)
        self.assertIn("always() && steps.scrape.outcome == 'failure'", workflow)


if __name__ == '__main__':
    unittest.main()
