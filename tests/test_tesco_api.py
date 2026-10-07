"""Regression coverage from captured Tesco Online prices and promotions."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scrapers import tesco
from scrapers.common import FIELDNAMES

FIXTURE = Path(__file__).parent / "fixtures" / "tesco_api_products.json"
NOW = datetime.fromisoformat("2026-10-07T08:00:00+02:00")


class TescoApiTests(unittest.TestCase):
    def setUp(self):
        self.products = json.loads(FIXTURE.read_text())

    def test_actual_discounts_loyalty_and_loose_produce(self):
        rows = tesco.extract_tesco_offers(self.products, NOW)
        by_name = {r['product_name']: r for r in rows}
        self.assertEqual(len(rows), 4)
        cauliflower = by_name['Květák']
        self.assertEqual(cauliflower['price'], 24.9)
        self.assertEqual(cauliflower['old_price'], 59.9)
        self.assertTrue(cauliflower['loyalty_required'])
        self.assertEqual(cauliflower['price_per_piece'], 24.9)
        pumpkin = by_name['Dýně Hokkaido']
        self.assertEqual(pumpkin['price'], 19.9)
        self.assertEqual(pumpkin['price_per_kg'], 19.9)
        self.assertEqual(pumpkin['old_price'], 49.9)
        kaki = by_name['Kakichurma']
        self.assertEqual(kaki['old_price'], 19.9)
        self.assertFalse(kaki['loyalty_required'])
        cherry = by_name['Tesco Rajčata cherry 250g']
        self.assertEqual(cherry['price'], 24.9)
        self.assertEqual(cherry['price_per_kg'], 99.6)
        for row in rows:
            self.assertEqual(set(row), set(FIELDNAMES))
            self.assertEqual(row['date_range'], '2026-10-07 - 2026-10-13')
            self.assertTrue(row['product_id'].startswith('tesco-online-'))

    def test_expired_multibuy_and_no_promotion_are_excluded(self):
        self.assertEqual(tesco.extract_tesco_offers(self.products,
                         datetime.fromisoformat('2026-12-01T08:00:00+01:00')), [])
        rows = tesco.extract_tesco_offers(self.products, NOW)
        self.assertNotIn('Mandarinky', [r['product_name'] for r in rows])
        self.assertNotIn('Tesco 100% pomerančová šťáva s dužninou 200ml',
                         [r['product_name'] for r in rows])

    def test_unsupported_loyalty_price_fails_before_publish(self):
        products = deepcopy(self.products)
        product = next(p for p in products if p['title'] == 'Květák')
        product['promotions'][0]['offerText'] = 'Výhodná cena s Clubcard'
        with self.assertRaises(ValueError):
            tesco.extract_tesco_offers(products, NOW)

    def test_empty_fetch_preserves_snapshot_and_history(self):
        with TemporaryDirectory() as d:
            path = Path(d) / 'tesco.csv'
            path.write_text('previous snapshot')
            with patch.object(tesco, 'fetch_tesco_offers', return_value=[]), patch.object(tesco, 'append_history') as history:
                with self.assertRaises(ValueError):
                    tesco.main(path)
                history.assert_not_called()
            self.assertEqual(path.read_text(), 'previous snapshot')

    def test_repeated_or_truncated_pages_fail(self):
        department = [{'name': n, 'id': n} for n in tesco.FOOD_DEPARTMENTS]
        for items, total in [([], 1), ([self.products[0]], 2)]:
            def fake_query(session, operation, document, variables):
                if operation == 'GetTaxonomy':
                    return {'taxonomy': department}
                self.assertIn('offers: false', document)
                return {'category': {'info': {'total': total, 'page': variables['page'], 'count': len(items)}, 'products': items}}
            with self.subTest(items=items), patch.object(tesco, 'query', side_effect=fake_query):
                with self.assertRaises(ValueError):
                    tesco.fetch_tesco_offers()


if __name__ == '__main__':
    unittest.main()
