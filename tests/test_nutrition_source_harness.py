import unittest
from unittest.mock import Mock

from nutrition_source_harness import find_exact, identity_matches, parse_nutrition_table


class SourceHarnessTests(unittest.TestCase):
    def test_identity_rejects_unrelated_variant(self):
        self.assertTrue(identity_matches("Sýr Cheddar Président 100 g", "Président Cheddar"))
        self.assertFalse(identity_matches("Sýr Mozzarella Galbani 125 g", "Galbani Ricotta"))

    def test_parse_nutrition_table(self):
        html = """<html><title>Example</title><h1>Example product</h1>
        <table><tr><th>Calories</th><td>123 kcal</td></tr>
        <tr><th>Protein</th><td>4,5 g</td></tr></table></html>"""
        name, values = parse_nutrition_table(html)
        self.assertEqual(name, "Example product")
        self.assertEqual(values["Calories"], {"value": 123.0, "unit": "kcal"})
        self.assertEqual(values["Protein"], {"value": 4.5, "unit": "g"})

    def test_page_lookup_returns_cache_ready_exact_result(self):
        response = Mock(status_code=200, url="https://example.test/product")
        response.text = """<h1>Actimel Danone 100 g</h1>
        <table><tr><th>Calories</th><td>73 kcal</td></tr></table>"""
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        result = find_exact("Actimel Danone 100 g", source_urls=["https://example.test/product"], brand="Danone", session=session)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["source_product"], "Actimel Danone 100 g")
        self.assertEqual(result["provenance"], "exact_match")


if __name__ == "__main__":
    unittest.main()
