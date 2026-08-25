"""Tests for data-to-HTML behavior in the static dashboard builder."""

import csv
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import build_site
from kupi_history import KUPI_PRICE_HISTORY_FIELDS
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
    def test_exact_links_are_store_specific_and_loyalty_prices_use_weight(self) -> None:
        self.assertEqual(
            build_site.exact_page_slug("Actimel Danone 100 g", "Albert"),
            "actimel_danone_100_g__albert",
        )
        row_data = history_row(product_name="Actimel Danone 100 g")
        self.assertEqual(build_site.normalized_offer_value(row_data, 69.9), (699.0, "kg"))
        link = build_site.exact_link(
            "Actimel Danone 100 g",
            "Actimel Danone 100 g",
            "Albert",
            {("Actimel Danone 100 g", "Albert"): "actimel_danone_100_g__albert"},
        )
        self.assertIn('href="exact/actimel_danone_100_g__albert.html"', link)

    def test_exact_page_shows_price_without_appended_unit(self) -> None:
        row_data = history_row(
            product_name="Zelí bílé kysané Albert 500 g",
            canonical_product_name="zeli",
            price="14.9",
            unit_price="2,98 Kč / 100 g",
            price_per_kg="29.8",
        )
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            write_csv(history, [row_data])
            with patch.object(build_site, "HISTORY_CSV", history), patch.object(
                build_site, "get_many", return_value={}
            ), patch.object(build_site, "get_many_exact", return_value={}), patch.object(
                build_site, "cache_image", return_value="img/veg.png"
            ):
                build_site.build()
            page = build_site.PRODUCTS_EXACT_DIR / "zelí_bílé_kysané_albert_500_g.html"
            html = page.read_text(encoding="utf-8")
        self.assertIn("14.9", html)
        self.assertNotIn("14.9 / 100 g", html)
        self.assertNotIn("29.8 / kg", html)

    def test_exact_page_shows_raw_price_without_appended_unit_when_source_unit_missing(self) -> None:
        row_data = history_row(
            product_name="Jogurt bílý 500 g",
            canonical_product_name="jogurt",
            price="14.9",
            unit_price="14,90 Kč",
            price_per_kg="",
            price_per_piece="",
        )
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            write_csv(history, [row_data])
            with patch.object(build_site, "HISTORY_CSV", history), patch.object(
                build_site, "get_many", return_value={}
            ), patch.object(build_site, "get_many_exact", return_value={}), patch.object(
                build_site, "cache_image", return_value="img/veg.png"
            ):
                build_site.build()
            page = build_site.PRODUCTS_EXACT_DIR / f"{build_site.slugify(row_data['product_name'])}.html"
            html = page.read_text(encoding="utf-8")
        self.assertIn("14.9", html)
        self.assertNotIn("14.9 / ks", html)
        self.assertNotIn("29.8 / kg", html)

    def test_liquid_offer_shows_per_litre_on_main_and_product_pages(self) -> None:
        row_data = history_row(
            product_name="Mléko trvanlivé 1 l",
            canonical_product_name="mleko_trvanlive",
            price="8.9",
            unit_price="8,90 Kč / 1 l",
            price_per_kg="",
            price_per_piece="",
        )
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            write_csv(history, [row_data])
            with patch.object(build_site, "HISTORY_CSV", history), patch.object(
                build_site, "get_many", return_value={}
            ), patch.object(build_site, "get_many_exact", return_value={}), patch.object(
                build_site, "cache_image", return_value="img/veg.png"
            ):
                html = build_site.build()
            exact_page = build_site.PRODUCTS_EXACT_DIR / "mléko_trvanlivé_1_l.html"
            product_page = build_site.PRODUCTS_DIR / "mleko_trvanlive.html"
            exact_html = exact_page.read_text(encoding="utf-8")
            product_html = product_page.read_text(encoding="utf-8")
        # Main (index) and product pages both recalculate to a per-litre price
        # and format it with two decimals.
        self.assertIn("8.90 / l", html)
        self.assertIn("8.90 / l", product_html)
        # Exact page keeps the retailer's original offer value, no unit appended.
        self.assertIn("8.9", exact_html)
        self.assertNotIn("8.9 / l", exact_html)
        self.assertNotIn("/ ks", exact_html)

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

    def test_build_keeps_overlapping_same_store_offers_for_the_next_two_weeks(self) -> None:
        today = date.today()
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            write_csv(
                history,
                [
                    history_row(
                        price="20.0",
                        unit_price="20,00 Kč / 1 kg",
                        price_per_kg="20.0",
                        date_range=f"{today.isoformat()} - {(today + timedelta(days=1)).isoformat()}",
                    ),
                    history_row(
                        price="30.0",
                        unit_price="30,00 Kč / 1 kg",
                        price_per_kg="30.0",
                        date_range=f"{(today + timedelta(days=2)).isoformat()} - {(today + timedelta(days=5)).isoformat()}",
                        scraped_at=f"{today.isoformat()}T06:01:00+02:00",
                    ),
                ],
            )
            with patch.object(build_site, "HISTORY_CSV", history), patch.object(
                build_site, "get_many", return_value={}
            ), patch.object(build_site, "write_product_pages"), patch.object(
                build_site, "cache_image", return_value="img/veg.png"
            ):
                html = build_site.build()

        self.assertIn("20.00", html)
        self.assertIn("30.00", html)
        self.assertEqual(html.count('class="ppcell" data-store="Albert"'), 2)
        self.assertIn('<div class="controls">', html)

    def test_build_collapses_repeated_scrapes_of_the_same_source_offer(self) -> None:
        today = date.today()
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            write_csv(
                history,
                [
                    history_row(
                        price="20.0",
                        unit_price="20,00 Kč / 1 kg",
                        price_per_kg="20.0",
                        date_range=f"{today.isoformat()} - {(today + timedelta(days=1)).isoformat()}",
                    ),
                    history_row(
                        price="20.0",
                        unit_price="20,00 Kč / 1 kg",
                        price_per_kg="20.0",
                        date_range=f"{(today + timedelta(days=1)).isoformat()} - {(today + timedelta(days=3)).isoformat()}",
                        scraped_at=f"{today.isoformat()}T06:01:00+02:00",
                    ),
                ],
            )
            with patch.object(build_site, "HISTORY_CSV", history), patch.object(
                build_site, "get_many", return_value={}
            ), patch.object(build_site, "write_product_pages"), patch.object(
                build_site, "cache_image", return_value="img/veg.png"
            ):
                html = build_site.build()

        self.assertEqual(html.count('class="ppcell" data-store="Albert"'), 1)
        self.assertIn(f'data-s="{(today + timedelta(days=1)).isoformat()}"', html)

    def test_build_collapses_same_visual_offer_from_different_source_products(self) -> None:
        today = date.today()
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            write_csv(
                history,
                [
                    history_row(
                        product_id="cherry-a",
                        product_name="Rajčata cherry A 0.25 kg",
                        canonical_product_name="rajcata_cherry",
                        price="34.9",
                        unit_price="139,60 Kč / 1 kg",
                        price_per_kg="139.6",
                    ),
                    history_row(
                        product_id="cherry-b",
                        product_name="Rajčata cherry B 0.25 kg",
                        canonical_product_name="rajcata_cherry",
                        price="34.9",
                        unit_price="139,60 Kč / 1 kg",
                        price_per_kg="139.6",
                        scraped_at=f"{today.isoformat()}T06:01:00+02:00",
                    ),
                ],
            )
            with patch.object(build_site, "HISTORY_CSV", history), patch.object(
                build_site, "get_many", return_value={}
            ), patch.object(build_site, "write_product_pages"), patch.object(
                build_site, "cache_image", return_value="img/veg.png"
            ):
                html = build_site.build()

        self.assertEqual(html.count('class="ppcell" data-store="Albert"'), 1)
        self.assertIn("139.60 / kg", html)

    def test_product_page_current_discounts_shows_name_first(self) -> None:
        """Variants grouped under one canonical product remain identifiable."""
        today = date.today()
        entries = [
            {
                "store": "Albert",
                "raw_name": "Rajčata cherry červená 250 g",
                "val": 34.9,
                "unit": "kg",
                "date_range": f"{today.isoformat()} - {(today + timedelta(days=2)).isoformat()}",
                "product_id": "cherry-red",
                "image_url": "https://img.example.test/cherry-red.jpg",
            },
            {
                "store": "Billa",
                "raw_name": "Rajčata cherry žlutá 250 g",
                "val": 39.9,
                "unit": "kg",
                "date_range": f"{today.isoformat()} - {(today + timedelta(days=2)).isoformat()}",
                "product_id": "cherry-yellow",
                "image_url": "https://img.example.test/cherry-yellow.jpg",
            },
        ]
        with TemporaryDirectory() as directory:
            products_dir = Path(directory) / "products"
            with patch.object(build_site, "PRODUCTS_DIR", products_dir), patch.object(
                build_site, "cache_image", return_value="img/veg.png"
            ):
                build_site.write_product_pages({"rajcata_cherry": entries}, {})
            html = (products_dir / "rajcata_cherry.html").read_text(encoding="utf-8")

        self.assertIn(
            "<th>Name</th><th>Store</th><th>Price</th><th>Discount days</th>",
            html,
        )
        self.assertIn('<a class="exact-link" href="exact/rajčata_cherry_červená_250_g.html"', html)
        self.assertIn('>Rajčata cherry červená 250 g</a></td>', html)
        self.assertIn('<a class="exact-link" href="exact/rajčata_cherry_žlutá_250_g.html"', html)
        self.assertIn('>Rajčata cherry žlutá 250 g</a></td>', html)

    def test_build_category_dropdown_defaults_to_produce(self) -> None:
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            write_csv(
                history,
                [
                    history_row(category="Ovoce a zelenina"),
                    history_row(
                        product_id="bread-1",
                        product_name="Chléb",
                        canonical_product_name="chleb",
                        category="Pečivo",
                        image_url="https://img.example.test/bread.jpg",
                    ),
                ],
            )
            with patch.object(build_site, "HISTORY_CSV", history), patch.object(
                build_site, "get_many", return_value={}
            ), patch.object(build_site, "write_product_pages"), patch.object(
                build_site, "cache_image", return_value="img/veg.png"
            ) as cache_image:
                html = build_site.build()

        self.assertIn('<select id="categoryFilter">', html)
        self.assertLess(html.index('value="all">All products'), html.index('value="Pečivo">Pečivo'))
        self.assertIn('value="Ovoce a zelenina" selected', html)
        self.assertIn('data-category="Pečivo"', html)
        cache_image.assert_any_call("bread-1", "https://img.example.test/bread.jpg")


class HistoricalTrendTests(unittest.TestCase):
    def test_kupi_graph_lowest_series_normalizes_units_and_excludes_future_dates(self) -> None:
        today = date.today()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "kupi_price_history.csv"
            rows = [
                {
                    "product_id": "2", "canonical_product_name": "banany", "product_name": "Banány",
                    "observed_date": (today - timedelta(days=2)).isoformat(), "series": "lowest_discount",
                    "price": "30", "currency": "Kč", "unit": "1 kg", "store_logo_url": "", "source": "kupi_graph",
                },
                {
                    "product_id": "2", "canonical_product_name": "banany", "product_name": "Banány",
                    "observed_date": (today - timedelta(days=1)).isoformat(), "series": "lowest_discount",
                    "price": "2.5", "currency": "Kč", "unit": "100 g", "store_logo_url": "", "source": "kupi_graph",
                },
                {
                    "product_id": "2", "canonical_product_name": "banany", "product_name": "Banány",
                    "observed_date": (today + timedelta(days=1)).isoformat(), "series": "lowest_discount",
                    "price": "10", "currency": "Kč", "unit": "1 kg", "store_logo_url": "", "source": "kupi_graph",
                },
                {
                    "product_id": "2", "canonical_product_name": "banany", "product_name": "Banány",
                    "observed_date": (today - timedelta(days=1)).isoformat(), "series": "regular_price",
                    "price": "40", "currency": "Kč", "unit": "1 kg", "store_logo_url": "", "source": "kupi_graph",
                },
            ]
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=KUPI_PRICE_HISTORY_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            series = build_site.kupi_graph_lowest_series(path, today)

        self.assertEqual(series[("banany", "kg")][today - timedelta(days=2)], 30.0)
        self.assertEqual(series[("banany", "kg")][today - timedelta(days=1)], 25.0)
        self.assertNotIn(today + timedelta(days=1), series[("banany", "kg")])

    def test_local_daily_price_overrides_kupi_graph_price_for_same_day(self) -> None:
        day = date.today()
        graph = {day - timedelta(days=1): 30.0, day: 25.0}
        local = {day: 20.0}

        merged = build_site.merge_daily_trends(graph, local)

        self.assertEqual(merged[day - timedelta(days=1)], 30.0)
        self.assertEqual(merged[day], 20.0)


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
