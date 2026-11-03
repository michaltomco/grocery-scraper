"""Official publisher discovery; onlineAt is never treated as offer validity."""
from datetime import date
import unittest
from unittest.mock import patch

import requests

from scrapers import albert_publitas


class PublitasTests(unittest.TestCase):
    def test_discovery_selects_recent_generic_supermarket_not_regions_or_magazines(self):
        def publication(slug, online):
            return {"slug": slug, "id": 1, "onlineAt": online}
        records = [
            publication("44sm_akcni_letak", "2026-10-26T07:00:18+01:00"),
            publication("45sm_akcni_letak", "2026-11-02T07:00:18+01:00"),
            publication("44sm_akcni_letak_bohnice", "2026-10-26T07:00:18+01:00"),
            publication("45hm_akcni_letak", "2026-11-02T07:00:18+01:00"),
            publication("albert-magazin-11-2026", "2026-11-02T07:00:18+01:00"),
            publication("43sm_akcni_letak", "2026-10-19T07:00:18+02:00"),
            publication("39sm_akcni_letak", "2026-09-21T07:00:18+02:00"),
        ]
        self.assertEqual(albert_publitas.discover_publications(records, date(2026, 11, 3)),
                         ["45sm_akcni_letak", "44sm_akcni_letak"])

    def test_fetch_uses_pdf_dates_not_publisher_dates_and_discards_expired_rows(self):
        records = [{"slug": "44sm_akcni_letak", "onlineAt": "2026-10-26T07:00:18+01:00"}]
        metadata = {"config": {"publicationId": 3470643, "slug": "44sm_akcni_letak",
                              "downloadPdfUrl": "/90263/3470643/pdfs/cb6e0b63-ae0c-44b5-b546-29029cd009da.pdf"}}
        actual = {"product_id": "test", "date_range": "2026-10-29 - 2026-11-03"}
        expired = {"product_id": "old", "date_range": "2026-10-20 - 2026-10-27"}
        with patch.object(albert_publitas, "get_json", side_effect=[records, metadata]), patch.object(
            albert_publitas, "get_pdf", return_value=b"%PDF-test"
        ) as pdf, patch.object(albert_publitas, "parse_pdf", return_value=[actual, expired]):
            rows = albert_publitas.fetch_publitas_products(("583",), today=date(2026, 11, 3))
        self.assertEqual(rows, [actual])
        self.assertTrue(pdf.call_args.args[0].startswith("https://view.publitas.com/90263/3470643/pdfs/"))

    def test_unverified_store_scope_fails_before_network(self):
        with patch.object(albert_publitas, "get_json") as fetch:
            with self.assertRaisesRegex(ValueError, "583"):
                albert_publitas.fetch_publitas_products(("829",))
        fetch.assert_not_called()

    def test_pdf_transport_rejects_html_even_with_http_success(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_content.return_value = [b"<html>Access denied</html>"]
        with patch.object(albert_publitas, "request_with_retry", return_value=response):
            with self.assertRaisesRegex(ValueError, "not a PDF"):
                albert_publitas.get_pdf("https://view.publitas.com/90263/1/pdfs/test.pdf")

    def test_no_applicable_offers_is_failure_not_empty_success(self):
        with patch.object(albert_publitas, "get_json", return_value=[]):
            with self.assertRaisesRegex(ValueError, "snapshot preserved"):
                albert_publitas.fetch_publitas_products(("583",), today=date(2026, 11, 3))

    def test_external_or_mismatched_pdf_url_is_rejected(self):
        for path in ["https://evil.example/prices.pdf", "/90263/999/pdfs/cb6e0b63-ae0c-44b5-b546-29029cd009da.pdf"]:
            records = [{"slug": "44sm_akcni_letak", "onlineAt": "2026-10-26T07:00:18+01:00"}]
            metadata = {"config": {"publicationId": 3470643, "slug": "44sm_akcni_letak", "downloadPdfUrl": path}}
            with patch.object(albert_publitas, "get_json", side_effect=[records, metadata]), patch.object(
                albert_publitas, "get_pdf"
            ) as pdf, self.assertRaises(ValueError):
                albert_publitas.fetch_publitas_products(("583",), today=date(2026, 11, 3))
            pdf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
