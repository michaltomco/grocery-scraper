import unittest

from retailer_manufacturer_nutrition import exact_identity, parse_page


class RetailerManufacturerNutritionTests(unittest.TestCase):
    def test_exact_identity_requires_distinctive_tokens(self):
        self.assertTrue(exact_identity("Actimel Danone 100 g", "Actimel Danone 100 g", "Danone"))
        self.assertFalse(exact_identity("Sýr Mozzarella Galbani 125 g", "Galbani Ricotta", "Galbani"))
        self.assertFalse(exact_identity("Sýry Billa 100 g", "BILLA Cottage sýr", "Billa"))

    def test_parse_jsonld_and_nutrition_table(self):
        page = """<html><head><title>Example</title>
        <script type='application/ld+json'>{"@type":"Product","name":"Actimel Danone 100 g"}</script>
        </head><body><table>
        <tr><th>Calories</th><td>73 kcal</td></tr>
        <tr><th>Protein</th><td>2,9 g</td></tr></table></body></html>"""
        name, values, url = parse_page(page, "https://example.test/product")
        self.assertEqual(name, "Actimel Danone 100 g")
        self.assertEqual(values["Calories"], {"value": 73.0, "unit": "kcal"})
        self.assertEqual(values["Protein"], {"value": 2.9, "unit": "g"})
        self.assertEqual(url, "https://example.test/product")


if __name__ == "__main__":
    unittest.main()
