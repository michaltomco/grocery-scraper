"""Regression and characterization tests for the shared scraper core."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scrapers.common import (
    FIELDNAMES,
    KUPI_FOOD_CATEGORIES,
    KupiStoreConfig,
    category_configs,
    run_kupi_food_scraper,
    append_history,
    canonical_product_name,
    extract_kupi_products,
    is_active_offer,
    merge_csvs,
    normalize_unit_price,
    parse_validity,
    read_csv,
    store_color,
    store_initial,
    store_logo,
    write_csv,
)


def row(**overrides: object) -> dict[str, object]:
    """Build a complete CSV row while keeping individual test cases readable."""
    value: dict[str, object] = {field: "" for field in FIELDNAMES}
    value.update(
        store="Albert",
        product_id="123",
        product_name="Jablka 1 kg",
        canonical_product_name="jablka",
        price="29.9",
        currency="Kč",
        unit_price="29,90 Kč / 1 kg",
        price_per_kg="29.9",
        date_range="2026-08-22 - 2026-08-25",
        url="https://example.test/product",
        scraped_at="2026-08-23T06:00:00+02:00",
    )
    value.update(overrides)
    return value


class ActiveOfferTests(unittest.TestCase):
    def test_malformed_date_range_is_retained_instead_of_crashing(self) -> None:
        """Bad upstream data must not take the generated site offline."""
        self.assertTrue(is_active_offer("valid until someday", date(2026, 8, 23)))

    def test_offer_is_active_through_its_end_date(self) -> None:
        self.assertTrue(is_active_offer("2026-08-22 - 2026-08-23", date(2026, 8, 23)))

    def test_expired_offer_is_not_active(self) -> None:
        self.assertFalse(is_active_offer("2026-08-20 - 2026-08-22", date(2026, 8, 23)))

    def test_far_future_offer_is_not_active(self) -> None:
        self.assertFalse(is_active_offer("2026-09-10 - 2026-09-11", date(2026, 8, 23)))


class NormalizationTests(unittest.TestCase):
    def test_canonical_name_preserves_avocado_subcategories(self) -> None:
        self.assertEqual(
            canonical_product_name("Avokádo Ready to eat Bio 1 ks"),
            "avokado_ready_to_eat_bio",
        )

    def test_canonical_name_groups_cherry_and_vine_tomatoes(self) -> None:
        self.assertEqual(canonical_product_name("Rajčata cherry 250 g"), "rajcata_cherry")
        self.assertEqual(canonical_product_name("Rajčata keříková 250 g"), "rajcata_cherry")

    def test_canonical_name_keeps_watermelon_separate_from_generic_melon(self) -> None:
        self.assertEqual(canonical_product_name("Meloun vodní 1 kg"), "meloun_vodni")
        self.assertEqual(canonical_product_name("Meloun cukrový 1 kg"), "meloun")

    def test_unit_price_normalizes_grams_and_pieces(self) -> None:
        self.assertEqual(normalize_unit_price("12,50 Kč / 100 g")["price_per_kg"], 125.0)
        self.assertEqual(normalize_unit_price("39,80 Kč / 2 ks")["price_per_piece"], 19.9)

    def test_validity_parses_czech_numeric_range(self) -> None:
        with patch("scrapers.common.datetime") as mocked_datetime:
            mocked_datetime.now.return_value.date.return_value = date(2026, 8, 23)
            self.assertEqual(
                parse_validity("pá 21. 8. – ne 23. 8."),
                ("2026-08-21", "2026-08-23"),
            )


class StoreBrandTests(unittest.TestCase):
    def test_known_stores_have_consistent_color_initial_and_svg_logo(self) -> None:
        for store, color, initial in (
            ("Lidl", "#facc15", "L"),
            ("Tesco", "#f87171", "T"),
            ("Albert", "#60a5fa", "A"),
            ("Billa", "#cc1f2c", "B"),
        ):
            with self.subTest(store=store):
                self.assertEqual(store_color(store), color)
                self.assertEqual(store_initial(store), initial)
                self.assertIn(f'aria-label="{store}"', store_logo(store))
                self.assertIn(f"fill=\"{color}\"", store_logo(store))


class HistoryAndMergeTests(unittest.TestCase):
    def test_food_category_runner_combines_category_snapshots(self) -> None:
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "albert.csv"
            base = KupiStoreConfig(
                store="Albert",
                url="https://www.kupi.cz/slevy/ovoce-a-zelenina/albert",
                csv_path=snapshot,
                store_location="Prague",
                loyalty_program="Můj Albert",
            )
            produce = KupiStoreConfig(**{**base.__dict__, "category": "Ovoce a zelenina"})
            bakery = KupiStoreConfig(**{**base.__dict__, "category": "Pečivo"})
            produce_row = row(category="Ovoce a zelenina")
            bakery_row = row(product_id="bread-1", product_name="Chléb", category="Pečivo")
            with patch("scrapers.common.category_configs", return_value=[produce, bakery]), patch(
                "scrapers.common.fetch_kupi_products", side_effect=[[produce_row], [bakery_row]]
            ), patch("scrapers.common.append_history") as append:
                run_kupi_food_scraper(base)

            rows = read_csv(snapshot)
            self.assertEqual({item["category"] for item in rows}, {"Ovoce a zelenina", "Pečivo"})
            append.assert_called_once()
            self.assertEqual(len(append.call_args.args[1]), 2)

    def test_empty_fetch_preserves_existing_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "albert.csv"
            existing = row()
            write_csv(snapshot, [existing])
            config = KupiStoreConfig(
                store="Albert",
                url="https://example.test/albert",
                csv_path=snapshot,
                store_location="Prague",
                loyalty_program="Můj Albert",
            )
            with patch("scrapers.common.fetch_kupi_products", return_value=[]), patch(
                "scrapers.common.append_history"
            ) as append:
                from scrapers.common import run_kupi_scraper
                run_kupi_scraper(config)

            self.assertEqual(read_csv(snapshot), [{field: str(existing.get(field, "")) for field in FIELDNAMES}])
            append.assert_not_called()

    def test_history_keeps_daily_rescrapes_but_deduplicates_identical_rows(self) -> None:
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            first = row(scraped_at="2026-08-22T06:00:00+02:00")
            same_day_duplicate = row(scraped_at="2026-08-22T06:00:00+02:00")
            next_day = row(scraped_at="2026-08-23T06:00:00+02:00")

            append_history(history, [first, same_day_duplicate, next_day])

            rows = read_csv(history)
            self.assertEqual(len(rows), 2)
            self.assertEqual({item["scraped_at"][:10] for item in rows}, {"2026-08-22", "2026-08-23"})

    def test_history_backfills_image_for_an_existing_key(self) -> None:
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            no_image = row(image_url="")
            with_image = row(image_url="https://img.example.test/apple.jpg")

            append_history(history, [no_image])
            append_history(history, [with_image])

            rows = read_csv(history)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["image_url"], "https://img.example.test/apple.jpg")

    def test_merge_deduplicates_same_offer_from_input_files(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            first = directory_path / "first.csv"
            second = directory_path / "second.csv"
            merged = directory_path / "merged.csv"
            duplicate = row()
            unique = row(product_id="999", product_name="Hrušky 1 kg", canonical_product_name="hrusky")
            write_csv(first, [duplicate])
            write_csv(second, [duplicate, unique])

            result = merge_csvs([first, second], merged)

            self.assertEqual(len(result), 2)
            self.assertEqual([item["product_id"] for item in read_csv(merged)], ["999", "123"])


class KupiExtractionTests(unittest.TestCase):
    def test_category_configs_cover_all_food_categories_for_store(self) -> None:
        base = KupiStoreConfig(
            store="Albert",
            url="https://www.kupi.cz/slevy/ovoce-a-zelenina/albert",
            csv_path=Path("albert.csv"),
            store_location="Prague",
            loyalty_program="Můj Albert",
        )

        configs = category_configs(base)

        self.assertEqual(len(configs), len(KUPI_FOOD_CATEGORIES))
        self.assertEqual(configs[0].category, "Ovoce a zelenina")
        self.assertEqual(configs[0].url, "https://www.kupi.cz/slevy/ovoce-a-zelenina/albert")
        self.assertIn("https://www.kupi.cz/slevy/pecivo/albert", [config.url for config in configs])

    def test_extract_products_reads_price_dates_loyalty_and_image(self) -> None:
        html = """
        <div class="product--wrap" data-product-id="123">
          <div class="product_image"><a href="/product/apple"><img data-src="/images/apple.jpg"></a></div>
          <div class="product_name"><h2><a title="Jablka Gala" href="/product/apple">Jablka Gala</a><span class="nowrap">1 kg</span></h2></div>
        </div>
        <div class="discount_row" data-product="123" data-discount="456">
          <div class="discounts_shop_name"><a>Albert Hypermarket</a></div>
          <span class="discount_price_value">29,90 Kč</span>
          <span class="discount_percentage">- 50 %</span>
          <span class="price_per_unit">29,90 Kč / 1 kg</span>
          <span class="discounts_validity">pá 21. 8. – ne 23. 8.</span>
          Platí pro členy klubu
        </div>
        """
        config = KupiStoreConfig(
            store="Albert",
            url="https://www.kupi.cz/slevy/ovoce-a-zelenina/albert",
            csv_path=Path("albert.csv"),
            store_location="Prague",
            loyalty_program="Můj Albert",
            category="Pečivo",
        )
        with patch("scrapers.common.today_timestamp", return_value="2026-08-23T06:00:00+02:00"), patch(
            "scrapers.common.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.return_value.date.return_value = date(2026, 8, 23)
            rows = extract_kupi_products(html, config)

        self.assertEqual(len(rows), 1)
        extracted = rows[0]
        self.assertEqual(extracted["product_name"], "Jablka Gala 1 kg")
        self.assertEqual(extracted["category"], "Pečivo")
        self.assertEqual(extracted["canonical_product_name"], "jablka")
        self.assertEqual(extracted["price"], 29.9)
        self.assertEqual(extracted["old_price"], 59.8)
        self.assertEqual(extracted["price_per_kg"], 29.9)
        self.assertTrue(extracted["loyalty_required"])
        self.assertEqual(extracted["loyalty_program"], "Můj Albert")
        self.assertEqual(extracted["date_range"], "2026-08-21 - 2026-08-23")
        self.assertEqual(extracted["image_url"], "https://www.kupi.cz/images/apple.jpg")


if __name__ == "__main__":
    unittest.main()
