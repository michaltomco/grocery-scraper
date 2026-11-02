"""BILLA must never publish an unvalidated or third-party refresh."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scrapers import billa, billa_pdf


class BillaDailySafetyTests(unittest.TestCase):
    def test_daily_fails_without_network_or_snapshot_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "billa.csv"
            snapshot.write_bytes(b"previous verified snapshot\n")
            config = replace(billa.CONFIG, csv_path=snapshot)
            with patch.object(billa, "CONFIG", config), patch(
                "requests.sessions.Session.request",
                side_effect=AssertionError("BILLA must not make unvalidated requests"),
            ):
                with self.assertRaisesRegex(RuntimeError, "BILLA direct source.*not validated"):
                    billa.main()
            self.assertEqual(snapshot.read_bytes(), b"previous verified snapshot\n")
            self.assertEqual(billa.CONFIG.store, "Billa")

    def test_discovers_exact_pdf_link_from_official_html_without_pinned_dates(self):
        html = (Path(__file__).parent / "fixtures/billa/official-download.html").read_text()
        urls = billa_pdf.discover_pdf_urls(html)
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("https://view.publitas.com/64069/2709538/pdfs/"))
        self.assertIn("response-content-disposition=attachment", urls[0])
        changed = html.replace("2709538", "9999999")
        self.assertEqual(billa_pdf.discover_pdf_urls(changed), [urls[0].replace("2709538", "9999999")])

    def test_experimental_entry_points_cannot_fetch_or_publish_stale_offers(self):
        with patch("requests.sessions.Session.request", side_effect=AssertionError("network disabled")):
            for entry_point in (billa_pdf.main, billa_pdf.run_billa_scrape):
                with self.subTest(entry_point=entry_point.__name__):
                    with self.assertRaisesRegex(billa.BillaScrapeError, "BILLA direct source.*not validated"):
                        entry_point()


if __name__ == "__main__":
    unittest.main()
