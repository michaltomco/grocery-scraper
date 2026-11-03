"""Real 44sm PDF text spans, stripped of images/irrelevant PDF metadata.

Only the PDF decoding boundary is replaced: all layout/price/condition parsing
runs against the original text, font sizes and bounding boxes.
"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scrapers import albert_pdf as parser

FIXTURE = Path(__file__).parent / 'fixtures/albert_44sm_cover_spans.json'
URL = 'https://view.publitas.com/albert/44sm_akcni_letak/'
STAMP = '2026-11-03T10:00:00+01:00'


class AlbertPDFTests(unittest.TestCase):
    def setUp(self):
        self.cover = json.loads(FIXTURE.read_text())

    def extract(self, cover=None, **kwargs):
        self.assertIsNotNone(parser, 'coordinate-aware PDF parser is missing')
        with patch.object(parser, '_read_cover', return_value=cover or self.cover):
            return parser.extract_pdf_products(
                b'%PDF-fixture', kwargs.pop('publication_slug', '44sm_akcni_letak'),
                kwargs.pop('source_url', URL), scraped_at=STAMP, **kwargs)

    def test_verified_cover_recovers_only_five_audited_food_cards(self):
        rows = self.extract()
        self.assertEqual(len(rows), 5)
        expected = {'Vepřová': 49.90, 'Kuřecí': 89.90, 'Česká chuť Vejce': 44.90,
                    'Česká chuť Máslo': 27.90, 'Svijanský': 8.90}
        for prefix, price in expected.items():
            row, = [r for r in rows if r['product_name'].startswith(prefix)]
            self.assertEqual(row['price'], price)
            self.assertEqual(row['date_range'], '2026-10-29 - 2026-11-03')
            self.assertEqual(row['scraped_at'], STAMP)
            self.assertFalse(row['loyalty_required'])
            self.assertEqual(row['url'], URL + 'page/1')
            self.assertTrue(row['category'])
            self.assertIn('44sm_akcni_letak-p1-', row['product_id'])

    def test_beer_preserves_volume_deposit_and_daily_limit_not_reference_price(self):
        row, = [r for r in self.extract() if r['product_name'].startswith('Svijanský')]
        self.assertIn('0,5 l', row['product_name'])
        self.assertIn('záloha na láhev 3 Kč', row['discount_label'])
        self.assertIn('40 ks / den', row['discount_label'])
        self.assertEqual(row['unit_price'], '17.8 Kč / 1 l')
        self.assertEqual(row['price_per_piece'], '')
        self.assertEqual(row['old_price'], '')

    def test_exact_rows_keep_retailer_stated_unit_price_including_rounding(self):
        rows = self.extract()
        egg, = [r for r in rows if 'Vejce' in r['product_name']]
        butter, = [r for r in rows if 'Máslo' in r['product_name']]
        beer, = [r for r in rows if 'Svijanský' in r['product_name']]
        self.assertEqual(egg['unit_price'], '2.5 Kč / 1 ks')
        self.assertEqual(egg['price_per_piece'], 2.5)
        self.assertEqual(butter['unit_price'], '11.16 Kč / 100 g')
        self.assertEqual(beer['unit_price'], '17.8 Kč / 1 l')

    def test_reused_weekly_slug_does_not_accept_another_year_or_publication(self):
        changed = copy.deepcopy(self.cover)
        for span in changed['spans']:
            if span['bbox'][1] < 30:
                span['text'] = span['text'].replace('2026', '2027')
        with self.assertRaisesRegex(ValueError, 'audited'):
            self.extract(changed)
        with self.assertRaisesRegex(ValueError, 'audited'):
            self.extract(source_url='https://view.publitas.com/90263/9999999/pdfs/example.pdf')

    def test_unknown_loyalty_fails_closed(self):
        self.cover['spans'] = [s for s in self.cover['spans']
                               if 'dostupná všem zákazníkům' not in s['text']]
        with self.assertRaisesRegex(ValueError, 'loyalty'):
            self.extract()

    def test_price_pair_in_label_column_is_not_a_featured_price(self):
        for s in self.cover['spans']:
            if s['font'] == 'AlbertDraft22Crossed-Reg' and s['text'] in ('49', '90') and 440 < s['bbox'][1] < 443 and s['bbox'][0] < 150:
                s['bbox'][0] -= 75
                s['bbox'][2] -= 75
        self.assertFalse(any(r['product_name'].startswith('Vepřová') for r in self.extract()))

    def test_pack_ranges_and_multipacks_are_not_relabelled_as_single_packs(self):
        for quantity in ('10–18 ks', '2 × 18 ks', '18 ks / 20 ks'):
            with self.subTest(quantity=quantity):
                changed = copy.deepcopy(self.cover)
                for s in changed['spans']:
                    if s['text'].startswith('• 18 ks'):
                        s['text'] = s['text'].replace('18 ks', quantity, 1)
                self.assertFalse(any('Vejce' in r['product_name'] for r in self.extract(changed)))

    def test_added_offer_conditions_are_not_silently_dropped(self):
        for text in ('Pouze 30. 10.', 'Cena s aplikací Můj Albert', 'při nákupu 2 balení'):
            with self.subTest(text=text):
                changed = copy.deepcopy(self.cover)
                changed['spans'].append(dict(text=text, font='AlbertText-RegularConden', size=8,
                                             bbox=[320, 470, 460, 480]))
                self.assertFalse(any('Máslo' in r['product_name'] for r in self.extract(changed)))

    def test_source_links_are_official_page_links_or_pdf_page_fragments(self):
        pdf_url = 'https://view.publitas.com/90263/3470643/pdfs/example.pdf'
        self.assertEqual(self.extract(source_url=pdf_url)[0]['url'], pdf_url + '#page=1')
        self.assertEqual(self.extract(source_url=URL + 'page/8')[0]['url'], URL + 'page/1')
        for url in ('https://example.com/albert/', 'https://view.publitas.com/other/44sm_akcni_letak/',
                    'https://view.publitas.com/albert/wrong/', 'http://view.publitas.com/albert/44sm_akcni_letak/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.extract(source_url=url)

    def test_upcoming_real_cover_recovers_five_audited_cards_with_own_conditions(self):
        cover = json.loads(FIXTURE.with_name('albert_45sm_cover_spans.json').read_text())
        rows = self.extract(cover, publication_slug='45sm_akcni_letak',
                            source_url='https://view.publitas.com/albert/45sm_akcni_letak/')
        self.assertEqual(len(rows), 5)
        for prefix, price, quantity, limit in (
            ('Vepřová', 69.90, '1 kg', '5 bal. / den'),
            ('Kuřecí', 109.90, '1 kg', '5 bal. / den'),
            ('Mandarinky', 21.90, '1 kg', ''),
            ('Albert Vejce', 24.90, '10 ks', '6 bal. / den'),
            ('Velkopopovický', 12.90, '0,5 l', ''),
        ):
            row, = [r for r in rows if r['product_name'].startswith(prefix)]
            self.assertEqual(row['price'], price)
            self.assertTrue(row['product_name'].endswith(quantity))
            self.assertIn(limit, row['discount_label'])
            self.assertEqual(row['date_range'], '2026-11-04 - 2026-11-10')
            self.assertFalse(row['loyalty_required'])
        beer, = [r for r in rows if r['product_name'].startswith('Velkopopovický')]
        self.assertIn('záloha na láhev 3 Kč', beer['discount_label'])

    def test_unreviewed_page_size_is_rejected_instead_of_reusing_coordinates(self):
        self.cover['width'] = 600
        with self.assertRaisesRegex(ValueError, 'layout'):
            self.extract()

    def test_reversed_or_conflicting_cover_validity_is_rejected(self):
        for text in ('Od čtvrtka 29. 11. do 3. 11. 2026',
                     'Od 29. 10. do 3. 11. 2026 Od 30. 10. do 3. 11. 2026'):
            with self.subTest(text=text):
                changed = copy.deepcopy(self.cover)
                for s in changed['spans']:
                    if s['text'].startswith('Od čtvrtka'):
                        s['text'] = text
                with self.assertRaisesRegex(ValueError, 'validity'):
                    self.extract(changed)

    def test_unassigned_page_wide_validity_constraint_is_not_inherited(self):
        self.cover['spans'].append(dict(text='Pouze v pátek 30. 10.', font='AlbertHeadline-Black',
                                       size=15, bbox=[220, 110, 440, 130]))
        with self.assertRaisesRegex(ValueError, 'constraint'):
            self.extract()

    def test_bullet_reference_price_is_labelled_separately_from_sale_price(self):
        beer, = [r for r in self.extract() if r['product_name'].startswith('Svijanský')]
        self.assertIn('Nejnižší cena za 30 dní: 10,90 Kč', beer['discount_label'])
        self.assertNotIn('3 Kč 10,90', beer['discount_label'])
        self.assertEqual(beer['price'], 8.90)

    def test_absent_featured_price_never_falls_back_to_regular_or_reference_price(self):
        self.cover['spans'] = [s for s in self.cover['spans'] if not (
            s['font'] == 'AlbertDraft22Crossed-Reg' and s['text'] == '8' and s['size'] > 50)]
        rows = self.extract()
        self.assertEqual(len(rows), 4)
        self.assertFalse(any(r['product_name'].startswith('Svijanský') for r in rows))

    def test_duplicate_price_anchors_are_ambiguous_not_extra_products(self):
        anchors = [copy.deepcopy(s) for s in self.cover['spans'] if
                   s['font'] == 'AlbertDraft22Crossed-Reg' and 245 < s['bbox'][1] < 249
                   and s['size'] > 30 and s['bbox'][0] < 200]
        self.cover['spans'].extend(anchors)
        self.assertFalse(any(r['product_name'].startswith('Svijanský') for r in self.extract()))

    def test_units_limits_and_butter_have_no_invented_limit(self):
        rows = self.extract()
        egg, = [r for r in rows if 'Vejce' in r['product_name']]
        self.assertTrue(egg['product_name'].endswith('18 ks'))
        self.assertIn('5 bal. / den', egg['discount_label'])
        for prefix, price in (('Vepřová', 49.9), ('Kuřecí', 89.9)):
            row, = [r for r in rows if r['product_name'].startswith(prefix)]
            self.assertEqual(row['price_per_kg'], price)
            self.assertIn('5 bal. / den', row['discount_label'])
        butter, = [r for r in rows if 'Máslo' in r['product_name']]
        self.assertEqual(butter['price_per_kg'], 111.6)
        self.assertNotIn('bal.', butter['discount_label'])
        self.assertTrue(butter['product_name'].endswith('250 g'))

    def test_unknown_publications_and_empty_verified_subsets_fail(self):
        with self.assertRaises(ValueError):
            self.extract(publication_slug='46sm_akcni_letak',
                         source_url='https://view.publitas.com/albert/46sm_akcni_letak/')
        self.cover['spans'] = [s for s in self.cover['spans'] if s['bbox'][1] < 65 or s['bbox'][1] > 800]
        with self.assertRaisesRegex(ValueError, 'No verified'):
            self.extract()

    def test_real_pdf_decoder_reports_invalid_bytes(self):
        with self.assertRaisesRegex(ValueError, 'cannot be decoded'):
            parser.extract_pdf_products(b'not a PDF', '44sm_akcni_letak', URL)

    def test_ids_are_independent_of_span_reading_order(self):
        before = {r['product_name']: r['product_id'] for r in self.extract()}
        self.cover['spans'].reverse()
        after = {r['product_name']: r['product_id'] for r in self.extract()}
        self.assertEqual(before, after)

    def test_nonpositive_sale_price_or_pack_size_is_not_an_offer(self):
        for field in ('price', 'quantity'):
            changed = copy.deepcopy(self.cover)
            for s in changed['spans']:
                if field == 'price' and s['font'] == 'AlbertDraft22Crossed-Reg' and 245 < s['bbox'][1] < 249 and s['bbox'][0] < 200:
                    s['text'] = '0' if s['text'] == '8' else '00'
                if field == 'quantity' and s['text'].startswith('• 18 ks'):
                    s['text'] = s['text'].replace('18 ks', '0 ks', 1)
            rows = self.extract(changed)
            with self.subTest(field=field):
                self.assertFalse(any(r['product_name'].startswith('Svijanský' if field == 'price' else 'Česká chuť Vejce') for r in rows))


if __name__ == '__main__':
    unittest.main()
