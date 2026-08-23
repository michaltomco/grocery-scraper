"""Tests for data-to-HTML behavior in the static dashboard builder."""

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import build_site
from scrapers.common import FIELDNAMES, write_csv


def history_row(**overrides: object) -> dict[str, object]:
    today = date.today()
    value: dict[str, object] = {field: "" for field in FIELDNAMES}
    value.update(
        store="Albert",
        product_id="apple-1",
        product_name="Jablka Gala 1 kg",
        canonical_product_name="jablka",
        price="29.9",
        currency="Kč",
        unit_price="29,90 Kč / 1 kg",
        price_per_kg="29.9",
        date_range=f"{today.isoformat()} - {(today + timedelta(days=2)).isoformat()}",
        url="https://example.test/apple",
        scraped_at=f"{today.isoformat()}T06:00:00+02:00",
    )
    value.update(overrides)
    return value


class DashboardBuilderTests(unittest.TestCase):
    def test_build_keeps_malformed_date_rows_without_crashing(self) -> None:
        """A malformed Kupi validity string must not make the site unavailable."""
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            write_csv(history, [history_row(date_range="valid until someday")])
            with patch.object(build_site, "HISTORY_CSV", history), patch.object(
                build_site, "get_many", return_value={}
            ), patch.object(build_site, "write_product_pages"), patch.object(
                build_site, "cache_image", return_value="img/veg.png"
            ):
                html = build_site.build()

        self.assertIn("Jablka", html)
        self.assertIn("Last run:", html)

    def test_build_shows_active_offers_and_excludes_expired_ones(self) -> None:
        today = date.today()
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            write_csv(
                history,
                [
                    history_row(product_name="Jablka Active 1 kg"),
                    history_row(
                        product_id="carrot-1",
                        product_name="Mrkev Expired 1 kg",
                        canonical_product_name="mrkev",
                        date_range=f"{(today - timedelta(days=3)).isoformat()} - {(today - timedelta(days=1)).isoformat()}",
                    ),
                ],
            )
            with patch.object(build_site, "HISTORY_CSV", history), patch.object(
                build_site, "get_many", return_value={}
            ), patch.object(build_site, "write_product_pages"), patch.object(
                build_site, "cache_image", return_value="img/veg.png"
            ):
                html = build_site.build()

        self.assertIn('href="products/jablka.html"', html)
        self.assertNotIn('href="products/mrkev.html"', html)
        self.assertIn(f"Last run: {today.isoformat()}", html)


class SiteHelperTests(unittest.TestCase):
    def test_normalized_falls_back_to_product_weight_when_unit_price_is_missing(self) -> None:
        self.assertEqual(
            build_site.normalized({"price": "49,90", "product_name": "Brambory 1 kg"}),
            (49.9, "kg"),
        )
        self.assertEqual(
            build_site.normalized({"price": "12.5", "product_name": "Avokádo 1 ks"}),
            (12.5, "ks"),
        )

    def test_czech_date_range_formatter_collapses_same_day_and_omits_repeated_year(self) -> None:
        self.assertEqual(build_site.fmt_cz("2026-08-05 - 2026-08-05"), "5.8.2026")
        self.assertEqual(build_site.fmt_cz("2026-08-05 - 2026-08-09"), "5.8 – 9.8.2026")

    def test_rda_calculations_convert_micrograms_and_milligrams(self) -> None:
        self.assertEqual(build_site.rda_percent("Vitamin C", 45, "mg"), 50)
        self.assertEqual(build_site.rda_percent("Vitamin A", 450, "µg"), 50)
        self.assertEqual(build_site.rda_percent("Vitamin A", 157, "ug"), 17)
        self.assertEqual(build_site.rda_amount_in_unit("Vitamin A", "mg"), 0.9)

    def test_vitamin_a_iu_without_rae_does_not_receive_an_rda_percentage(self) -> None:
        self.assertIsNone(build_site.rda_percent("Vitamin A", 3130, "iu"))
        self.assertIsNone(build_site.rda_amount_in_unit("Vitamin A", "iu"))

    def test_html_escaping_and_sparkline_do_not_emit_raw_markup(self) -> None:
        self.assertEqual(build_site.esc('<apple & "pear">'), "&lt;apple &amp; &quot;pear&quot;&gt;")
        svg = build_site.sparkline([])
        self.assertEqual(svg, "")
        svg = build_site.sparkline([(build_site.datetime(2026, 8, 1), 10.0)])
        self.assertIn("<svg", svg)
        self.assertIn("<circle", svg)


if __name__ == "__main__":
    unittest.main()
