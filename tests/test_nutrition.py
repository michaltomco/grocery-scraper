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

    def test_usda_rejects_kiwi_juice_for_kiwi_query(self) -> None:
        class Response:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {
                    "foods": [
                        {
                            "description": "Beverages, Kiwi Strawberry Juice Drink",
                            "foodNutrients": [
                                {"nutrientName": "Energy", "value": 50, "unitName": "KCAL"},
                            ],
                        }
                    ]
                }

        with patch.object(nutrition.requests, "get", return_value=Response()):
            self.assertEqual(nutrition.fetch_usda_nutrition("kiwi"), {})

    def test_onion_query_rejects_unrelated_falafel_fallback(self) -> None:
        """A candidate without the queried produce term cannot supply its macros."""
        class Response:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {
                    "products": [
                        {
                            "product_name": "Middle Eastern Falafels",
                            "categories_tags": ["en:plant-based-foods"],
                            "nutriments": {"energy-kcal_100g": 221, "fat_100g": 9.9},
                        }
                    ]
                }

        with patch.object(nutrition, "fetch_usda_nutrition", return_value={}), patch.object(
            nutrition.requests, "get", return_value=Response()
        ):
            result = nutrition.fetch_nutrition("cibule_bio_nature_s_promise")

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["values"], {})

    def test_usda_second_pass_replaces_open_food_macros(self) -> None:
        """When USDA recovers, generic produce must not retain fallback macros."""
        class Response:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {
                    "products": [
                        {
                            "product_name": "Lettuce, Cos Or Romaine, Raw",
                            "categories_tags": ["en:lettuce"],
                            "nutriments": {"energy-kcal_100g": 1, "fat_100g": 0.02},
                        }
                    ]
                }

        usda = {
            "source": "USDA FoodData Central",
            "source_product": "Lettuce, cos or romaine, raw",
            "values": {"Calories": {"value": 17, "unit": "kcal"}, "Fat": {"value": 0.3, "unit": "g"}},
        }
        with patch.object(nutrition, "fetch_usda_nutrition", side_effect=[{}, usda]), patch.object(
            nutrition.requests, "get", return_value=Response()
        ):
            result = nutrition.fetch_nutrition("salat_little_gem")

        self.assertEqual(result["source"], "USDA FoodData Central")
        self.assertEqual(result["values"]["Calories"], {"value": 17, "unit": "kcal"})

    def test_ambiguous_salad_mix_is_marked_unavailable_without_lookup(self) -> None:
        with patch.object(nutrition, "fetch_usda_nutrition") as fetch:
            result = nutrition.fetch_nutrition("salat_baby_listy_mix")

        fetch.assert_not_called()
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["values"], {})

    def test_strawberry_compote_is_marked_unavailable_without_lookup(self) -> None:
        with patch.object(nutrition, "fetch_usda_nutrition") as fetch:
            result = nutrition.fetch_nutrition("kompot_jahody_giana")

        fetch.assert_not_called()
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["values"], {})

    def test_polnicek_resolves_to_lambs_lettuce(self) -> None:
        self.assertEqual(nutrition.resolve_nutrition_query("salat_polnicek"), "lamb lettuce raw")

    def test_crispy_salad_resolves_to_iceberg_lettuce(self) -> None:
        self.assertEqual(nutrition.resolve_nutrition_query("salat_krupavy_crispy"), "iceberg lettuce raw")


class NutritionCacheTests(unittest.TestCase):
    def test_get_many_can_use_cache_without_network_requests(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "nutrition_cache.json"
            cache.write_text("{}", encoding="utf-8")
            with patch.object(nutrition, "CACHE_PATH", cache), patch.object(
                nutrition, "fetch_nutrition"
            ) as fetch:
                result = nutrition.get_many({"new_product"}, fetch_missing=False)

        fetch.assert_not_called()
        self.assertNotIn("new_product", result)

    def test_get_many_keeps_current_successful_entries_without_fetching(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "nutrition_cache.json"
            cache.write_text(
                json.dumps(
                    {
                        "jablka": {
                            "status": "found",
                            "schema_version": nutrition.CACHE_VERSION,
                            "query": "apple",
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

    def test_get_many_refreshes_open_food_entry_that_does_not_match_query(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "nutrition_cache.json"
            cache.write_text(
                json.dumps(
                    {
                        "cibule_bio_nature_s_promise": {
                            "status": "found",
                            "schema_version": nutrition.CACHE_VERSION,
                            "query": "onion",
                            "source": "Open Food Facts + USDA FoodData Central",
                            "source_product": "Middle Eastern Falafels",
                            "values": {"Calories": {"value": 221, "unit": "kcal"}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            replacement = {"status": "found", "schema_version": nutrition.CACHE_VERSION, "values": {}}
            with patch.object(nutrition, "CACHE_PATH", cache), patch.object(
                nutrition, "fetch_nutrition", return_value=replacement
            ) as fetch:
                result = nutrition.get_many({"cibule_bio_nature_s_promise"})

        fetch.assert_called_once_with("cibule_bio_nature_s_promise")
        self.assertEqual(result["cibule_bio_nature_s_promise"]["status"], "found")

    def test_get_many_refreshes_usda_juice_entry_for_raw_kiwi(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "nutrition_cache.json"
            cache.write_text(
                json.dumps(
                    {
                        "kiwi": {
                            "status": "found",
                            "schema_version": nutrition.CACHE_VERSION,
                            "query": "kiwi",
                            "source": "USDA FoodData Central",
                            "source_product": "Beverages, Kiwi Strawberry Juice Drink",
                            "values": {"Calories": {"value": 50, "unit": "kcal"}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            replacement = {"status": "found", "schema_version": nutrition.CACHE_VERSION, "values": {}}
            with patch.object(nutrition, "CACHE_PATH", cache), patch.object(
                nutrition, "fetch_nutrition", return_value=replacement
            ) as fetch:
                result = nutrition.get_many({"kiwi"})

        fetch.assert_called_once_with("kiwi")
        self.assertEqual(result["kiwi"]["status"], "found")

    def test_get_many_refreshes_legacy_mixed_open_food_and_usda_entry(self) -> None:
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "nutrition_cache.json"
            cache.write_text(
                json.dumps(
                    {
                        "salat_rodinny_mix": {
                            "status": "found",
                            "schema_version": nutrition.CACHE_VERSION,
                            "query": "lettuce raw",
                            "source": "Open Food Facts + USDA FoodData Central",
                            "source_product": "Lettuce, Cos Or Romaine, Raw",
                            "values": {"Calories": {"value": 1, "unit": "kcal"}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            replacement = {"status": "found", "schema_version": nutrition.CACHE_VERSION, "values": {}}
            with patch.object(nutrition, "CACHE_PATH", cache), patch.object(
                nutrition, "fetch_nutrition", return_value=replacement
            ) as fetch:
                result = nutrition.get_many({"salat_rodinny_mix"})

        fetch.assert_called_once_with("salat_rodinny_mix")
        self.assertEqual(result["salat_rodinny_mix"]["status"], "found")


if __name__ == "__main__":
    unittest.main()
