"""Regression tests using public Můj Albert app responses captured on 2026-10-06."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import requests

from scrapers import albert
from scrapers.common import FIELDNAMES, append_history, read_csv

FIXTURE = Path(__file__).parent / "fixtures" / "albert_app_leaflets.json"


class AlbertAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(FIXTURE.read_text())

    def rows(self, store_id: str = "829") -> list[dict]:
        return albert.extract_albert_products(self.payload, store_id)

    def test_actual_prices_dates_identity_and_shared_schema(self) -> None:
        row = self.rows()[0]
        self.assertEqual(set(row), set(FIELDNAMES))
        self.assertEqual(row["product_name"], "Mandarinky 1 kg")
        self.assertEqual(row["canonical_product_name"], "mandarinky")
        self.assertEqual(row["price"], 39.9)
        self.assertEqual(row["old_price"], 69.9)
        self.assertEqual(row["price_per_kg"], 39.9)
        self.assertEqual(row["date_range"], "2026-09-30 - 2026-10-06")
        self.assertEqual(row["product_id"], "albert-829-20440701")
        self.assertIn("storeId=829", row["url"])
        self.assertTrue(row["image_url"].startswith(albert.API_BASE))
        self.assertFalse(row["loyalty_required"])

    def test_member_price_keeps_an_ordinary_offer(self) -> None:
        rows = [row for row in self.rows() if "brokolice" in row["product_name"]]
        self.assertEqual(len(rows), 2)
        ordinary, member = rows
        self.assertEqual(ordinary["price_per_piece"], 24.9)
        self.assertEqual(ordinary["loyalty_price"], "")
        self.assertEqual(member["price_per_piece"], 19.9)
        self.assertEqual(member["loyalty_price"], 19.9)
        self.assertTrue(member["loyalty_required"])
        self.assertEqual(member["loyalty_program"], "Můj Albert")
        self.assertEqual(member["discount_label"], "-55% Můj Albert")

    def test_current_and_next_week_are_preserved(self) -> None:
        rows = [row for row in self.rows() if row["canonical_product_name"] == "mandarinky"]
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["date_range"] for row in rows}, {
            "2026-09-30 - 2026-10-06", "2026-10-07 - 2026-10-13"
        })
        self.assertEqual({row["price"] for row in rows}, {39.9, 29.9, 26.9})
        self.assertEqual(len({row["product_id"] for row in rows}), 1)

    def test_packaged_and_weighted_products_use_priced_volume(self) -> None:
        rows = self.rows()
        grapes = next(row for row in rows if "Hrozny tmavé" in row["product_name"])
        self.assertEqual(grapes["price_per_kg"], 59.8)
        sausage = next(row for row in rows if "Královské párky" in row["product_name"])
        self.assertEqual(sausage["unit_price"], "21.9 Kč / 100 g")
        self.assertEqual(sausage["price_per_kg"], 219)
        eggs = next(row for row in rows if "vejce" in row["product_name"])
        self.assertEqual(eggs["price_per_piece"], round(eggs["price"] / 10, 2))
        drink = next(row for row in rows if "Jägermeister" in row["product_name"])
        self.assertTrue(drink["product_name"].endswith("1 l"))
        self.assertTrue(drink["unit_price"].endswith("/ 1 l"))

    def test_affiliated_variant_has_its_own_price_and_quantity(self) -> None:
        rows = self.rows()
        parent = next(row for row in rows if row["product_name"] == "Mlynářský bochník 0.5 kg")
        self.assertEqual(parent["price"], 49.9)
        self.assertEqual(parent["old_price"], "")
        variants = [row for row in rows if row["product_name"] == "Mlynářská brioška 400g"]
        self.assertEqual(len(variants), 1)
        variant = variants[0]
        self.assertEqual(variant["price"], 33.9)
        self.assertEqual(variant["price_per_kg"], 84.75)
        self.assertFalse(variant["loyalty_required"])
        self.assertEqual(variant["image_url"], "")

    def test_store_identity_survives_shared_history_deduplication(self) -> None:
        with TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            with patch("scrapers.albert.today_timestamp", return_value="2026-10-06T06:00:00+02:00"):
                first, second = self.rows("829"), self.rows("743")
            append_history(history, first + second)
            self.assertEqual(len(read_csv(history)), len(first) + len(second))

    def test_duplicates_collapse_and_nonfood_is_excluded(self) -> None:
        original = self.rows()
        self.payload["current"]["items"].append(deepcopy(self.payload["current"]["items"][0]))
        self.assertEqual(len(self.rows()), len(original))
        self.assertFalse(any(row["category"] == "Drogerie" for row in original))

    def test_missing_next_week_is_valid(self) -> None:
        self.payload["next"] = None
        self.assertTrue(self.rows())
        self.assertTrue(all(row["date_range"].endswith("2026-10-06") for row in self.rows()))

    def test_related_variant_does_not_replace_primary_product_name(self) -> None:
        primary = self.payload["current"]["items"][0]
        other = self.payload["current"]["items"][1]
        other["affiliateProducts"].append({
            "goldId": primary["goldId"], "name": "Short variant name",
            "pricedVolume": primary["pricedVolume"],
            "regularPrice": primary["price"]["original"],
            "promoPrice": primary["price"]["discount"],
            "discountPercentage": primary["price"]["discountPercentage"],
        })
        row = self.rows()[0]
        self.assertEqual(row["product_name"], "Mandarinky 1 kg")

    def test_invalid_prices_dates_categories_and_units_fail(self) -> None:
        mutations = (
            {"price": {**self.payload["current"]["items"][0]["price"], "discount": float("nan")}},
            {"price": {**self.payload["current"]["items"][0]["price"], "discount": 0}},
            {"validFrom": "2026-10-09", "validTo": "2026-10-06"},
            {"pricedVolume": "unknown"},
            {"leafletCategory": "NEW_CATEGORY"},
            {"promoForMember": True},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = deepcopy(self.payload)
                payload["current"]["items"][0].update(mutation)
                with self.assertRaises(ValueError):
                    albert.extract_albert_products(payload, "829")

    def test_empty_fetch_or_request_failure_preserves_snapshot_and_history(self) -> None:
        for result in ([], requests.Timeout("timeout")):
            with self.subTest(result=result), TemporaryDirectory() as directory:
                snapshot = Path(directory) / "albert.csv"
                snapshot.write_text("existing")
                with patch("scrapers.albert.fetch_albert_products",
                           **({"side_effect": result} if isinstance(result, Exception) else {"return_value": result})), patch(
                    "scrapers.albert.append_history"
                ) as append:
                    with self.assertRaises((ValueError, requests.Timeout)):
                        albert.main(snapshot)
                self.assertEqual(snapshot.read_text(), "existing")
                append.assert_not_called()

    def test_all_selected_stores_must_succeed_before_snapshot_is_written(self) -> None:
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "albert.csv"
            snapshot.write_text("existing")
            stores = [{"storeId": "829", "name": "Hypermarket"}, {"storeId": "743", "name": "Supermarket"}]
            with patch("scrapers.albert.fetch_albert_stores", return_value=stores), patch(
                "scrapers.albert.fetch_json", side_effect=[self.payload, requests.Timeout("timeout")]
            ), patch("scrapers.albert.append_history") as append:
                with self.assertRaises(requests.Timeout):
                    albert.main(snapshot, store_ids=("829", "743"))
            self.assertEqual(snapshot.read_text(), "existing")
            append.assert_not_called()

    def test_runner_configuration_and_no_history_preview(self) -> None:
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "albert.csv"
            with patch.object(albert, "CONFIG", replace(albert.CONFIG, csv_path=snapshot)), patch(
                "scrapers.albert.fetch_albert_products", return_value=self.rows()
            ), patch("scrapers.albert.append_history") as append:
                albert.main(history_path=None)
            self.assertTrue(read_csv(snapshot))
            append.assert_not_called()

    def test_environment_and_explicit_store_selection(self) -> None:
        with patch.dict(os.environ, {"ALBERT_STORE_IDS": " 743,829,743 "}):
            self.assertEqual(albert.selected_store_ids(), ("743", "829"))
            self.assertEqual(albert.selected_store_ids(("829",)), ("829",))
        with patch.dict(os.environ, {"ALBERT_STORE_IDS": ""}):
            with self.assertRaises(ValueError):
                albert.selected_store_ids()

    def test_unknown_store_is_rejected_before_offer_request(self) -> None:
        with patch("scrapers.albert.fetch_albert_stores", return_value=[]), patch(
            "scrapers.albert.fetch_json"
        ) as fetch:
            with self.assertRaisesRegex(ValueError, "Unknown or deleted"):
                albert.fetch_albert_products(("829",))
            fetch.assert_not_called()

    def test_http_status_and_response_shape_are_checked(self) -> None:
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("403")
        with patch("scrapers.albert.requests.get", return_value=response):
            with self.assertRaises(requests.HTTPError):
                albert.fetch_json(albert.STORES_URL)
        response.raise_for_status.side_effect = None
        response.json.return_value = []
        with patch("scrapers.albert.requests.get", return_value=response):
            with self.assertRaises(ValueError):
                albert.fetch_json(albert.STORES_URL)


if __name__ == "__main__":
    unittest.main()
