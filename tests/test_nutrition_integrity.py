"""Real contamination regressions; no network or generated site required."""
import unittest
from unittest.mock import Mock, patch
import nutrition


class GroupedNutritionIntegrityTests(unittest.TestCase):
    def test_processed_slug_never_gets_raw_profile(self):
        for slug in ('relax_ananas', 'bramborove_hranolky_menu_inspirace',
                     'kukurice_predvarena', 'koreni_paprika_sl adka_vitana'):
            with self.subTest(slug=slug), patch.object(nutrition, 'fetch_usda_nutrition') as fetch:
                self.assertEqual(nutrition.fetch_nutrition(slug)['status'], 'not_found')
                fetch.assert_not_called()

    def test_grouped_existing_bad_profile_is_unavailable_offline(self):
        bad = {'status': 'found', 'source': 'USDA FoodData Central',
               'source_product': 'Corn, sweet, raw',
               'values': {'Calories': {'value': 86, 'unit': 'kcal'}}}
        with patch.object(nutrition, 'load_cache', return_value={'kukurice_predvarena': bad}), patch.object(
                nutrition, '_load_nutridata', return_value={}):
            result = nutrition.get_many({'kukurice_predvarena'}, fetch_missing=False)
        self.assertEqual(result['kukurice_predvarena']['values'], {})

    def test_audit_flags_zero_only_energy_for_review_without_deleting_it(self):
        from audit_nutrition_integrity import audit_cache
        entry = {'status': 'found', 'source_product': 'Banány', 'provenance': 'exact_match',
                 'source_url': 'https://example.org/banana',
                 'values': {'Calories': {'value': 0, 'unit': 'kcal'}}}
        report = audit_cache({'exact': {'exact:Banány 1 kg': entry}})
        self.assertEqual(report['counts']['definite_conflicts'], 0)
        self.assertEqual(report['counts']['review_required'], 1)

    def test_audit_distinguishes_definite_conflict_from_unreviewed_names(self):
        from audit_nutrition_integrity import audit_cache
        cache = {'exact': {
            'exact:Jablka': {'status': 'found', 'source_product': 'Kapsička jablka', 'values': {}},
            'exact:Jogurt Activia Danone 120 g': {'status': 'found',
                'source_product': 'Activia Bílá jogurt', 'values': {}}}}
        audit = audit_cache(cache)
        self.assertEqual(audit['counts']['definite_conflicts'], 1)
        self.assertEqual(audit['counts']['review_required'], 1)

    def test_curated_pickle_cannot_override_raw_cucumber_even_offline(self):
        pickle = {'status': 'found', 'nutridata_curated': True,
                  'source_product': 'Okurky sterilované Hamé', 'provenance': 'category_proxy',
                  'values': {'Energy': {'value': 100, 'unit': 'kJ'}}}
        raw = {'status': 'found', 'query': 'cucumber', 'schema_version': nutrition.CACHE_VERSION,
               'source': 'USDA FoodData Central', 'source_product': 'Cucumber, raw',
               'values': {'Calories': {'value': 15, 'unit': 'kcal'}}}
        with patch.object(nutrition, 'load_cache', return_value={'okurka': raw}), patch.object(
                nutrition, '_load_nutridata', return_value={'okurka': pickle}):
            result = nutrition.get_many({'okurka'}, fetch_missing=False)
        self.assertEqual(result['okurka']['source_product'], 'Cucumber, raw')


class MacroIntegrityTests(unittest.TestCase):
    def test_impossible_usda_values_are_not_selected(self):
        response = Mock()
        response.json.return_value = {'foods': [{'description': 'Pineapple, raw',
            'foodNutrients': [
                {'nutrientName': 'Energy', 'value': 0, 'unitName': 'KCAL'},
                {'nutrientName': 'Protein', 'value': 20, 'unitName': 'G'}]}]}
        with patch.object(nutrition.requests, 'get', return_value=response):
            self.assertEqual(nutrition.fetch_usda_nutrition('pineapple'), {})

    def test_impossible_energy_rejected_in_cached_and_fetched_exact(self):
        values = {'Calories': {'value': 0, 'unit': 'kcal'},
                  'Protein': {'value': 14.8, 'unit': 'g'}}
        entry = {'status': 'found', 'source_product': 'Anglická slanina speciál',
                 'values': values, 'provenance': 'exact_match'}
        with patch.object(nutrition, 'load_cache', return_value={'exact': {
                'exact:Anglická slanina speciál 100 g': entry}}):
            result = nutrition.get_many_exact({'Anglická slanina speciál 100 g'}, fetch_missing=False)
        self.assertEqual(result['exact:Anglická slanina speciál 100 g']['values'], {})
        response = Mock()
        response.json.return_value = {'products': [{
            'product_name': 'Anglická slanina speciál', 'code': '123',
            'nutriments': {'energy-kcal_100g': 0, 'proteins_100g': 14.8}}]}
        with patch.object(nutrition.requests, 'get', return_value=response):
            self.assertEqual(nutrition._off_exact('Anglická slanina speciál')['status'], 'not_found')

    def test_valid_raw_produce_and_bacon_are_not_rejected(self):
        for kcal, protein, fat in ((52, .3, .2), (337, 14.8, 30)):
            self.assertEqual(nutrition.nutrient_issues({
                'Calories': {'value': kcal, 'unit': 'kcal'},
                'Protein': {'value': protein, 'unit': 'g'},
                'Fat': {'value': fat, 'unit': 'g'}}), [])


class ExactIntegrityTests(unittest.TestCase):
    def test_cached_exact_rejects_conflicting_flavour_despite_shared_brand(self):
        entry = {'status': 'found', 'source_product': 'Jogurt jahodový Brand',
                 'provenance': 'exact_match', 'source_url': 'https://example.org/yogurt',
                 'values': {'Calories': {'value': 80, 'unit': 'kcal'}}}
        with patch.object(nutrition, 'load_cache', return_value={'exact': {
                'exact:Jogurt vanilkový Brand': entry}}):
            result = nutrition.get_many_exact({'Jogurt vanilkový Brand'}, fetch_missing=False)
        self.assertEqual(result['exact:Jogurt vanilkový Brand']['values'], {})
        self.assertEqual(result['exact:Jogurt vanilkový Brand']['quarantined_record'], entry)

    def test_category_proxy_is_never_exact_nutrition(self):
        entry = {'status': 'found', 'provenance': 'category_proxy',
                 'source_product': 'Jogurt', 'source_url': 'https://example.org/jogurt',
                 'values': {'Calories': {'value': 60, 'unit': 'kcal'}}}
        with patch.object(nutrition, 'load_cache', return_value={'exact': {'exact:Jogurt': entry}}):
            result = nutrition.get_many_exact({'Jogurt'}, fetch_missing=False)
        self.assertEqual(result['exact:Jogurt']['values'], {})
        with patch.object(nutrition, '_load_nutridata', return_value={'jogurt': entry}), patch.object(
                nutrition, '_off_exact', return_value={'status': 'not_found', 'values': {}}):
            self.assertEqual(nutrition.fetch_nutrition_exact('Jogurt')['status'], 'not_found')

    def test_unrelated_cached_exact_name_is_rejected(self):
        self.assertTrue(nutrition.identity_issues('Mléko Pilos', {
            'source_product': 'Chléb žitný', 'status': 'found', 'provenance': 'exact_match'}))

    def test_new_source_contains_retained_usda_id_url(self):
        response = Mock()
        response.json.return_value = {'foods': [{'fdcId': 169124,
            'description': 'Pineapple, raw', 'foodNutrients': [
                {'nutrientName': 'Energy', 'value': 50, 'unitName': 'KCAL'}]}]}
        with patch.object(nutrition.requests, 'get', return_value=response):
            result = nutrition.fetch_nutrition('ananas')
        self.assertEqual(result['source_url'], 'https://fdc.nal.usda.gov/food-details/169124/nutrients')
        self.assertEqual(result['provenance'], 'raw_produce_profile')

    def test_exact_lookup_rejects_fruit_inside_pouch(self):
        response = Mock()
        response.json.return_value = {'products': [{
            'product_name': 'Kapsička s příchutí jahod, jablka a liči',
            'code': '123', 'nutriments': {'energy-kcal_100g': 63}}]}
        with patch.object(nutrition.requests, 'get', return_value=response):
            self.assertEqual(nutrition._off_exact('Jablka')['status'], 'not_found')

    def test_cached_exact_rejects_pouch_even_offline(self):
        entry = {'status': 'found', 'source': 'Open Food Facts',
                 'source_product': 'Kapsička s příchutí jahod, jablka a liči',
                 'source_url': 'https://example.org/123', 'provenance': 'exact_match',
                 'values': {'Calories': {'value': 63, 'unit': 'kcal'}}}
        cache = {'exact': {'exact:Jablka': entry}}
        with patch.object(nutrition, 'load_cache', return_value=cache):
            result = nutrition.get_many_exact({'Jablka'}, fetch_missing=False)
        self.assertEqual(result['exact:Jablka']['values'], {})
        self.assertEqual(entry['values']['Calories']['value'], 63)
from scrapers.common import canonical_product_name


class ProduceGroupingIntegrityTests(unittest.TestCase):
    def test_processing_quantity_cannot_disappear_into_raw_group(self):
        for label in ('Ananas 1 l', 'Ananas 565/340 g', 'Paprika červená 1 l'):
            with self.subTest(label=label):
                slug = canonical_product_name(label)
                self.assertNotIn(slug, {'ananas', 'paprika_cervena'})
                self.assertIsNone(nutrition.resolve_nutrition_query(slug))
        self.assertEqual(canonical_product_name('Ananas 1 ks'), 'ananas')

    def test_live_canned_pineapple_and_pomegranate_are_not_raw_pineapple_or_apple(self):
        for label in ('Giana Ananas kousky ve sladkém nálevu 565g',
                      'Giana Ananas plátky v ananasové šťávě 565g'):
            with self.subTest(label=label):
                self.assertNotEqual(canonical_product_name(label), 'ananas')
        self.assertEqual(canonical_product_name('Granátové jablko'), 'granatove_jablko')
        self.assertEqual(canonical_product_name('Granátová jablka 1 ks'), 'granatove_jablko')
        self.assertEqual(canonical_product_name('Jablka šťavnatá 1 kg'), 'jablka')

    def test_processed_pineapple_keeps_its_identity(self):
        for label in ('Caprio ANANAS 2 l', 'Relax ananas 1 l',
                      'CoolVitality jablko ananas směs 1 l', 'Rauch ananas džus 1 l',
                      'Kapsička jablka jahody', 'Jablka sušená 100 g',
                      'Giana ananas kousky 565/340 g', 'Loyd čaj ananas hruš.pyr.40g',
                      'Tesco Kandovaný ananas plátky 200g',
                      'Koření Paprika sladká Vitana 100 g'):
            with self.subTest(label=label):
                self.assertNotIn(canonical_product_name(label),
                                 {'ananas', 'jablka', 'jablka_ananas', 'jablka_jahody', 'paprika'})
        self.assertEqual(canonical_product_name('Mrkev kořenová 1 kg'), 'mrkev')
        self.assertEqual(canonical_product_name('Nektarinky čerstvé 1 kg'), 'nektarinky')
        self.assertEqual(nutrition.resolve_nutrition_query('nektarinky'), 'nectarine')
        self.assertEqual(canonical_product_name('Ananas 1 ks'), 'ananas')
        self.assertEqual(canonical_product_name('Jablka Golden Delicious 1 kg'), 'jablka')
        self.assertEqual(canonical_product_name('Rajčata cherry 250 g'), 'rajcata_cherry')
