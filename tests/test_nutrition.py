"""Tests for nutrition extraction and persistent-cache behavior."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import nutrition


class NutritionExtractionTests(unittest.TestCase):
    def test_extract_keeps_valid_nutrients_with_source_units(self) -> None:
        values = nutrition._extract(
            {
                "nutriments": {
                    "energy-kcal_100g": 52,
                    "proteins_100g": "0.3",
                    "vitamin-c_100g": 4.6,
                    "vitamin-c_100g_unit": "mg",
                    "iron_100g": -1,
                    "sugars_100g": "not a number",
                }
            }
        )

        self.assertEqual(values["Calories"], {"value": 52.0, "unit": "kcal"})
        self.assertEqual(values["Protein"], {"value": 0.3, "unit": "g"})
        self.assertEqual(values["Vitamin C"], {"value": 4.6, "unit": "mg"})
        self.assertNotIn("Iron", values)
        self.assertNotIn("Sugars", values)

    def test_corrupt_cache_is_treated_as_empty(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "nutrition_cache.json"
            cache.write_text("{not valid json", encoding="utf-8")
            with patch.object(nutrition, "CACHE_PATH", cache):
                self.assertEqual(nutrition.load_cache(), {})

    def test_usda_prefers_vitamin_a_rae_over_ambiguous_iu(self) -> None:
        """Red sweet pepper has both fields; RAE is the dietary comparison unit."""
        class Response:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {
                    "foods": [
                        {
                            "description": "Peppers, sweet, red, raw",
                            "foodNutrients": [
                                {"nutrientName": "Vitamin A, RAE", "value": 157, "unitName": "UG"},
                                {"nutrientName": "Vitamin A, IU", "value": 3130, "unitName": "IU"},
                                {"nutrientName": "Carotene, beta", "value": 1620, "unitName": "UG"},
                            ],
                        }
                    ]
                }

        with patch.object(nutrition.requests, "get", return_value=Response()):
            result = nutrition.fetch_usda_nutrition("red bell pepper raw")

        self.assertEqual(result["values"]["Vitamin A"], {"value": 157.0, "unit": "ug"})

    def test_lime_query_rejects_processed_tortilla_chip_fallback(self) -> None:
        """A generic lime query must never turn a produce page into snack data."""
        class Response:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {
                    "products": [
                        {
                            "product_name": "Rolled Corn Tortilla Chips Chili & Lime Flavored",
                            "categories_tags": ["en:salty-snacks"],
                            "nutriments": {"energy-kcal_100g": 535.71, "fat_100g": 28.57},
                        }
                    ]
                }

        with patch.object(nutrition, "fetch_usda_nutrition", return_value={}), patch.object(
            nutrition.requests, "get", return_value=Response()
        ):
            result = nutrition.fetch_nutrition("limety")

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["values"], {})


class NutritionCacheTests(unittest.TestCase):
    def test_get_many_keeps_current_successful_entries_without_fetching(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "nutrition_cache.json"
            cache.write_text(
                json.dumps(
                    {
                        "jablka": {
                            "status": "found",
                            "schema_version": nutrition.CACHE_VERSION,
                            "values": {"Calories": {"value": 52, "unit": "kcal"}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(nutrition, "CACHE_PATH", cache), patch.object(
                nutrition, "fetch_nutrition"
            ) as fetch:
                result = nutrition.get_many({"jablka"})

        fetch.assert_not_called()
        self.assertEqual(result["jablka"]["status"], "found")

    def test_get_many_fetches_new_product_and_persists_result(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "nutrition_cache.json"
            fetched = {
                "status": "found",
                "schema_version": nutrition.CACHE_VERSION,
                "query": "apple",
                "values": {"Calories": {"value": 52, "unit": "kcal"}},
            }
            with patch.object(nutrition, "CACHE_PATH", cache), patch.object(
                nutrition, "fetch_nutrition", return_value=fetched
            ) as fetch:
                result = nutrition.get_many({"jablka"})

            fetch.assert_called_once_with("jablka")
            self.assertEqual(result["jablka"], fetched)
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["jablka"], fetched)

    def test_get_many_retries_a_transient_error_entry(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "nutrition_cache.json"
            cache.write_text(
                json.dumps(
                    {
                        "jablka": {
                            "status": "error",
                            "schema_version": nutrition.CACHE_VERSION,
                            "values": {},
                        }
                    }
                ),
                encoding="utf-8",
            )
            replacement = {
                "status": "found",
                "schema_version": nutrition.CACHE_VERSION,
                "query": "apple",
                "values": {},
            }
            with patch.object(nutrition, "CACHE_PATH", cache), patch.object(
                nutrition, "fetch_nutrition", return_value=replacement
            ) as fetch:
                result = nutrition.get_many({"jablka"})

        fetch.assert_called_once_with("jablka")
        self.assertEqual(result["jablka"]["status"], "found")

    def test_get_many_refreshes_legacy_vitamin_a_iu_cache_entry(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "nutrition_cache.json"
            cache.write_text(
                json.dumps(
                    {
                        "paprika_cervena": {
                            "status": "found",
                            "schema_version": nutrition.CACHE_VERSION,
                            "values": {"Vitamin A": {"value": 3130, "unit": "iu"}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            replacement = {
                "status": "found",
                "schema_version": nutrition.CACHE_VERSION,
                "values": {"Vitamin A": {"value": 157, "unit": "ug"}},
            }
            with patch.object(nutrition, "CACHE_PATH", cache), patch.object(
                nutrition, "fetch_nutrition", return_value=replacement
            ) as fetch:
                result = nutrition.get_many({"paprika_cervena"})

        fetch.assert_called_once_with("paprika_cervena")
        self.assertEqual(result["paprika_cervena"]["values"]["Vitamin A"], {"value": 157, "unit": "ug"})

    def test_get_many_refreshes_processed_open_food_cache_entry(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "nutrition_cache.json"
            cache.write_text(
                json.dumps(
                    {
                        "limety": {
                            "status": "found",
                            "schema_version": nutrition.CACHE_VERSION,
                            "source": "Open Food Facts",
                            "source_product": "Rolled Corn Tortilla Chips Chili & Lime Flavored",
                            "values": {"Calories": {"value": 535.71, "unit": "kcal"}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            replacement = {"status": "not_found", "schema_version": nutrition.CACHE_VERSION, "values": {}}
            with patch.object(nutrition, "CACHE_PATH", cache), patch.object(
                nutrition, "fetch_nutrition", return_value=replacement
            ) as fetch:
                result = nutrition.get_many({"limety"})

        fetch.assert_called_once_with("limety")
        self.assertEqual(result["limety"]["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
