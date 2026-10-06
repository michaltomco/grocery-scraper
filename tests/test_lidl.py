"""Direct-source regression tests from captured Lidl.cz product cards."""

import html
import json
from pathlib import Path
from dataclasses import replace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scrapers.common import FIELDNAMES, read_csv
from scrapers.lidl import extract_lidl_products, main
from scrapers import lidl

FIXTURE = Path(__file__).parent / "fixtures" / "lidl_cards.json"


def cards_html(cards: list[dict]) -> str:
    return "".join(
        f'<div data-grid-data="{html.escape(json.dumps(card), quote=True)}"></div>'
        for card in cards
    )


class LidlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cards = json.loads(FIXTURE.read_text())

    def test_captured_prices_dates_and_units(self) -> None:
        rows = extract_lidl_products(cards_html(self.cards))
        banana, cabbage, rocket, basil = rows
        self.assertEqual(set(banana), set(FIELDNAMES))
        self.assertEqual(banana["price"], 19.9)
        self.assertEqual(banana["old_price"], 39.9)
        self.assertEqual(banana["discount_label"], "-50%")
        self.assertEqual(banana["price_per_kg"], 19.9)
        self.assertEqual(banana["date_range"], "2026-10-05 - 2026-10-07")
        self.assertEqual(banana["url"], "https://www.lidl.cz/p/banany/p10049379")
        self.assertTrue(banana["image_url"].startswith("https://"))
        self.assertFalse(banana["loyalty_required"])
        self.assertEqual(cabbage["old_price"], "")
        self.assertEqual(rocket["price"], 19.9)
        self.assertEqual(rocket["price_per_kg"], 159.2)
        self.assertEqual(basil["price_per_piece"], 34.9)

    def test_duplicates_collapse_but_distinct_validity_is_preserved(self) -> None:
        changed = {**self.cards[0], "storeEndDate": self.cards[0]["storeEndDate"] + 86400}
        rows = extract_lidl_products(cards_html([self.cards[0], self.cards[0], changed]))
        self.assertEqual(len(rows), 2)

    def test_empty_page_preserves_snapshot_and_history(self) -> None:
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "lidl.csv"
            snapshot.write_text("existing snapshot")
            with patch("scrapers.lidl.fetch_lidl_products", return_value=[]), patch(
                "scrapers.lidl.append_history"
            ) as append:
                with self.assertRaises(ValueError):
                    main(snapshot)
            self.assertEqual(snapshot.read_text(), "existing snapshot")
            append.assert_not_called()

    def test_invalid_or_conditional_cards_fail_before_writing(self) -> None:
        for pricing in (
            {**self.cards[0]["price"], "price": float("nan")},
            {**self.cards[0]["price"], "currencyCode": "EUR"},
            {**self.cards[0]["price"], "variantsHaveDifferentPrices": True},
        ):
            with self.subTest(pricing=pricing):
                with self.assertRaises(ValueError):
                    extract_lidl_products(cards_html([{**self.cards[0], "price": pricing}]))
        with self.assertRaises(json.JSONDecodeError):
            extract_lidl_products('<div data-grid-data="broken"></div>')

    def test_runner_writes_compatible_csv_and_history(self) -> None:
        rows = extract_lidl_products(cards_html(self.cards))
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "lidl.csv"
            history = Path(directory) / "history.csv"
            with patch("scrapers.lidl.fetch_lidl_products", return_value=rows):
                main(snapshot, history)
            self.assertEqual(len(read_csv(snapshot)), 4)
            by_id = lambda rows: sorted(rows, key=lambda row: row["product_id"])
            self.assertEqual(by_id(read_csv(snapshot)), by_id(read_csv(history)))

    def test_default_output_uses_runner_configuration(self) -> None:
        rows = extract_lidl_products(cards_html(self.cards))
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "lidl.csv"
            with patch.object(lidl, "CONFIG", replace(lidl.CONFIG, csv_path=snapshot)), patch(
                "scrapers.lidl.fetch_lidl_products", return_value=rows
            ):
                main(history_path=None)
            self.assertEqual(len(read_csv(snapshot)), 4)


if __name__ == "__main__":
    unittest.main()
