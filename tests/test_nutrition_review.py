"""Advisory review regression tests; fixtures are test-only, never cache input."""
import copy
import importlib
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module():
    assert (ROOT / 'nutrition_review.py').exists(), 'nutrition review implementation missing'
    return importlib.import_module('nutrition_review')


# Selected fields copied from verified-run/matches.json, grouped ananas pair.
# Snapshot is actual Laya pair output, not a claimed independently verified label.
PAIR = {
    'id': 'grouped:ananas:pair:e5c9e11c51d2076b56dd1529521357fb9da6f0f03841a9c5d6a07a1beb618d7d',
    'key': 'ananas', 'namespace': 'grouped', 'parent_cache_key': 'ananas',
    'product_text': 'Ananas extra sladký 1 ks',
    'source_product': 'Pineapple, raw, all varieties',
    'source': 'USDA FoodData Central', 'source_url': '', 'status': 'found',
    'values': {'Protein': {'value': 0.54, 'unit': 'g'},
               'Fat': {'value': 0.12, 'unit': 'g'},
               'Carbs': {'value': 13.1, 'unit': 'g'},
               'Calories': {'value': 50.0, 'unit': 'kcal'}},
    'retailer_joined': True, 'missing_identity': False, 'prob': 0.1121,
    'approval_allowed': False, 'usage': {'truncated': False},
}


class QueueTests(unittest.TestCase):
    def test_only_semantic_candidate_changes_invalidate_review(self):
        nr = module()
        def fingerprint(row):
            return nr.build_queue({'records': [row]})['candidates'][0]['fingerprint']
        original = fingerprint(PAIR)
        changed = copy.deepcopy(PAIR)
        changed.update(prob=.99, cache_path='/new/checkout/cache.json',
                       usage={'truncated': False, 'state_tokens': 79},
                       product_names=['another alias'], scraped_at='2026-11-04',
                       price=123, confidence=.99)
        self.assertEqual(original, fingerprint(changed))
        changed['source_url'] = 'https://example.org/new-source'
        self.assertNotEqual(original, fingerprint(changed))
        changed = copy.deepcopy(PAIR)
        changed['values']['Protein']['value'] = 1.23
        self.assertNotEqual(original, fingerprint(changed))

    def test_actual_pair_preserved_and_all_scores_routed(self):
        nr = module()
        second = copy.deepcopy(PAIR)
        second.update(id=PAIR['id'] + '-variant', prob=0.999)
        queue = nr.build_queue({'records': [second, PAIR]})
        self.assertEqual(len(queue['candidates']), 2)
        self.assertEqual(queue['candidates'][0]['record'], PAIR)
        self.assertTrue(all(c['requires_codex'] for c in queue['candidates']))
        self.assertTrue(all(c['approval_allowed'] is False for c in queue['candidates']))
        self.assertEqual(queue, nr.build_queue({'records': [PAIR, second]}))
        self.assertIn('missing_provenance', queue['candidates'][0]['deterministic_issues'])
        with self.assertRaises(ValueError):
            nr.build_queue({'records': [PAIR, PAIR]})
        with self.assertRaises(ValueError):
            nr.build_queue({'matches': [PAIR]})


class GuardTests(unittest.TestCase):
    def test_numeric_guards_and_subset_macros(self):
        nr = module()
        for val in (-1, float('nan'), float('inf'), True, '3'):
            row = copy.deepcopy(PAIR)
            row['values']['Protein']['value'] = val
            self.assertTrue(any('invalid_nutrient:Protein' == i for i in nr.deterministic_issues(row)))
        row = copy.deepcopy(PAIR)
        row['values'] = {k: {'value': 40, 'unit': 'g'} for k in ('Fat', 'Protein', 'Carbs')}
        self.assertIn('macro_total_over_100g', nr.deterministic_issues(row))
        row['values'] = {'Calories': {'value': 1001, 'unit': 'kcal'}}
        self.assertIn('implausible_kcal_per_100g', nr.deterministic_issues(row))
        row['values'] = {'Fat': {'value': 60, 'unit': 'g'}, 'Saturated fat': {'value': 40, 'unit': 'g'},
                         'Protein': {'value': 10, 'unit': 'g'}, 'Carbs': {'value': 31, 'unit': 'g'}}
        self.assertNotIn('macro_total_over_100g', nr.deterministic_issues(row))
        row['values']['Fat']['value'] = 101
        self.assertIn('over_100g:Fat', nr.deterministic_issues(row))
        row['values'] = []
        self.assertIn('invalid_values', nr.deterministic_issues(row))
        row['values'] = {'Fat': None}
        self.assertIn('invalid_nutrient:Fat', nr.deterministic_issues(row))

    def test_preparation_conflicts_never_bypassed_and_priority(self):
        nr = module()
        good = copy.deepcopy(PAIR)
        good.update(id='good', prob=.999, source_url='https://fdc.nal.usda.gov/food/1', provenance='generic')
        bad = copy.deepcopy(good)
        bad.update(id='bad', product_text='Ananas', source_product='Pineapple juice')
        queue = nr.build_queue({'records': [good, bad]})
        self.assertEqual(queue['candidates'][0]['candidate_id'], 'bad')
        self.assertIn('raw_produce_vs_processed_source', queue['candidates'][0]['deterministic_issues'])
        self.assertTrue(queue['candidates'][0]['requires_codex'])
        for prob in (True, -1, 2, float('nan'), None):
            good['prob'] = prob
            with self.assertRaises(ValueError):
                nr.build_queue({'records': [good]})

    def test_raw_profile_conflicts_cover_actual_tomato_variants(self):
        nr = module()
        # Actual current Laya retailer texts; test-only reduced snapshots.
        for label in ('Cirio Pasírovaná rajčata 350g', 'Fjk rajčata sušená 100g',
                      'Giana rajčata loupaná 400/240g', 'Gervais High Protein rajče a oregano 100g',
                      'Canned tomatoes', 'Dried tomatoes'):
            row = copy.deepcopy(PAIR)
            row.update(product_text=label, source='NutriData.cz', prob=.999,
                       source_product='Rajčata, rajče, různé druhy, syrová, průměr')
            self.assertIn('raw_profile_preparation_or_variant_conflict', nr.deterministic_issues(row), label)
            candidate = nr.build_queue({'records': [row]})['candidates'][0]
            result = nr.validate_decision(result_for(candidate, 'exact_match'), candidate)
            self.assertFalse(result['final_acceptance'])
            self.assertIn('raw_profile_preparation_or_variant_conflict', result['deterministic_issues'])
        row.update(product_text='Rajčata cherry 250 g')
        self.assertNotIn('raw_profile_preparation_or_variant_conflict', nr.deterministic_issues(row))


def result_for(candidate, classification='compatible_generic'):
    return {'candidate_id': candidate['candidate_id'], 'classification': classification,
            'evidence': [{'url': 'https://fdc.nal.usda.gov/food/169124',
                          'quote': 'Pineapple, raw: Protein 0.54 g per 100 g.'}],
            'explanation': 'Test-only external reviewer result; not live research.',
            'nutrition_values': []}


class DecisionTests(unittest.TestCase):
    def test_strict_advisory_decision_and_sourced_numeric_proposals(self):
        nr = module()
        self.assertTrue(callable(getattr(nr, 'validate_decision', None)), 'decision validator missing')
        candidate = nr.build_queue({'records': [PAIR]})['candidates'][0]
        raw = result_for(candidate)
        raw['nutrition_values'] = [{'nutrient': 'Protein', 'value': .54, 'unit': 'g',
                                    'basis': 'per_100g', 'source_url': raw['evidence'][0]['url'],
                                    'quote': raw['evidence'][0]['quote']}]
        decision = nr.validate_decision(raw, candidate)
        self.assertFalse(decision['approval_allowed'])
        self.assertFalse(decision['final_acceptance'])
        self.assertEqual(decision['evidence_status'], 'requires_verification')
        self.assertEqual(decision['nutrition_values'], raw['nutrition_values'])
        self.assertEqual(decision['nutrition_status'], 'unverified_proposal')
        for field, value in [('candidate_id', 'wrong'), ('classification', 'accepted'),
                             ('explanation', ''), ('evidence', []), ('approval_allowed', True)]:
            broken = copy.deepcopy(raw)
            broken[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                nr.validate_decision(broken, candidate)
        for url in ('file:///a', 'https://user:pw@example.com', 'https://', 'https://a b/x',
                    'https://example.com:bad', 'https://example.com/\nsecret',
                    'https://exa%mple.com', 'https://-bad.test', 'https://example..com'):
            broken = result_for(candidate)
            broken['evidence'][0]['url'] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                nr.validate_decision(broken, candidate)
        for field, value in [('value', 1.54), ('value', True), ('value', float('nan')),
                             ('unit', 'kg'), ('basis', 'serving'), ('source_url', 'https://other.test'),
                             ('quote', 'Protein 0.54 g per 100 g. NOT cited')]:
            broken = copy.deepcopy(raw)
            broken['nutrition_values'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                nr.validate_decision(broken, candidate)
        for classification in ('mismatch', 'insufficient_evidence'):
            broken = result_for(candidate, classification)
            broken['evidence'] = []
            self.assertEqual(nr.validate_decision(broken, candidate)['classification'], classification)


class WorkflowTests(unittest.TestCase):
    def test_prepare_dry_run_ingestion_and_cli_errors(self):
        nr = module()
        self.assertTrue(callable(getattr(nr, 'main', None)), 'CLI missing')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scores = root / 'matches.json'
            scores.write_text(json.dumps({'records': [PAIR]}))
            out = root / 'prepared'
            self.assertEqual(nr.main(['prepare', '--scores', str(scores), '--out-dir', str(out)]), 0)
            queue_path = out / 'queue.json'
            queue = json.loads(queue_path.read_text())
            with patch.object(nr, 'run_codex') as transport:
                self.assertEqual(nr.main(['review', '--queue', str(queue_path), '--out-dir', str(root / 'review'), '--dry-run']), 0)
                transport.assert_not_called()
            result = root / 'external.json'
            result.write_text(json.dumps(result_for(queue['candidates'][0])))
            with patch.object(nr, 'run_codex') as transport:
                self.assertEqual(nr.main(['review', '--queue', str(queue_path), '--out-dir', str(root / 'review'), '--input-result', str(result)]), 0)
                transport.assert_not_called()
            decisions = list((root / 'review').rglob('decision.json'))
            self.assertEqual(len(decisions), 1)
            self.assertFalse(json.loads(decisions[0].read_text())['approval_allowed'])
            self.assertEqual(nr.main(['review', '--queue', str(queue_path), '--out-dir', str(root / 'review'), '--limit', '-1']), 1)
            queue['candidates'][0]['record']['source_product'] = 'tampered'
            queue_path.write_text(json.dumps(queue))
            self.assertEqual(nr.main(['review', '--queue', str(queue_path), '--out-dir', str(root / 'review')]), 1)

    def test_bounded_resumable_reviews_and_changed_content(self):
        nr = module()
        self.assertTrue(callable(getattr(nr, 'review_queue', None)), 'review runner missing')
        second = copy.deepcopy(PAIR)
        second.update(id='another-pair', prob=.99)
        queue = nr.build_queue({'records': [PAIR, second]})
        with tempfile.TemporaryDirectory() as tmp:
            def response(attempt, prompt, model, timeout):
                candidate = json.loads((attempt / 'input.json').read_text())
                self.assertIn('Do not use Kupi', prompt)
                self.assertIn('not memory', prompt)
                return json.dumps(result_for(candidate))
            with patch.object(nr, 'run_codex', side_effect=response) as transport:
                summary = nr.review_queue(queue, Path(tmp), limit=1)
                self.assertEqual(summary['reviewed'], 1)
                self.assertEqual(transport.call_count, 1)
                summary = nr.review_queue(queue, Path(tmp), limit=1)
                self.assertEqual(summary['skipped'], 1)
                self.assertEqual(transport.call_count, 2)
                summary = nr.review_queue(queue, Path(tmp), limit=1)
                self.assertEqual(summary['skipped'], 2)
                self.assertEqual(transport.call_count, 2)
                changed = copy.deepcopy(PAIR)
                changed['source_url'] = 'https://example.org/new-evidence'
                nr.review_queue(nr.build_queue({'records': [changed]}), Path(tmp), limit=1)
                self.assertEqual(transport.call_count, 3)
            self.assertEqual(len(list(Path(tmp).rglob('input.json'))), 3)
            self.assertEqual(len(list(Path(tmp).rglob('prompt.txt'))), 3)
            self.assertEqual(len(list(Path(tmp).rglob('raw-final.json'))), 3)
            # Broken cached decisions never become trusted skip markers.
            # Select a decision in this queue, not an arbitrary filesystem entry
            # (which could belong to the changed-source queue above).
            decision = Path(tmp) / nr.review_cache_key(queue['candidates'][0]) / 'decision.json'
            self.assertTrue(decision.is_file())
            decision.write_text('{}')
            with patch.object(nr, 'run_codex', side_effect=response) as transport:
                nr.review_queue(queue, Path(tmp), limit=2)
                self.assertEqual(transport.call_count, 1)

    def test_ingestion_null_never_invokes_engine_and_strict_json(self):
        nr = module()
        queue = nr.build_queue({'records': [PAIR]})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'external.json'
            path.write_text('null')
            with patch.object(nr, 'run_codex') as transport:
                with self.assertRaises(ValueError):
                    nr.review_queue(queue, Path(tmp) / 'reports', input_result=path)
                transport.assert_not_called()
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.assertRaises(ValueError):
                nr.parse_json(text)

    def test_engine_failure_visible_and_retry_not_cached(self):
        nr = module()
        self.assertTrue(callable(getattr(nr, 'review_queue', None)), 'review runner missing')
        queue = nr.build_queue({'records': [PAIR]})
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(nr, 'run_codex', side_effect=RuntimeError('engine unavailable')) as transport:
                for _ in range(2):
                    summary = nr.review_queue(queue, Path(tmp))
                    self.assertEqual(summary['errors'], 1)
                self.assertEqual(transport.call_count, 2)
            self.assertFalse(list(Path(tmp).rglob('decision.json')))
            errors = list(Path(tmp).rglob('error.json'))
            self.assertEqual(len(errors), 2)
            self.assertIn('engine unavailable', errors[0].read_text())


class TransportTests(unittest.TestCase):
    def test_configured_adapter_spawns_only_web_helper_in_clean_scratch(self):
        nr = module()
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            attempt = Path(tmp)
            def run(argv, **kwargs):
                self.assertEqual(argv[0], '/configured/hermes/python')
                self.assertEqual(Path(argv[1]), ROOT / 'nutrition_web_reviewer.py')
                self.assertEqual(argv[2:], ['--model', 'gpt-6.1-sol', '--timeout', '12'])
                self.assertIs(kwargs['shell'], False)
                self.assertEqual(kwargs['input'], 'untrusted stdin prompt')
                self.assertEqual(kwargs['timeout'], 12)
                self.assertNotEqual(Path(kwargs['cwd']), ROOT)
                self.assertTrue(Path(kwargs['cwd']).is_dir())
                self.assertLessEqual(set(kwargs['env']), {'HOME', 'PATH', 'HERMES_HOME', 'TMPDIR'})
                self.assertNotIn('INJECTED_SECRET', kwargs['env'])
                return subprocess.CompletedProcess(argv, 0, '{"assistant":"final"}', 'verified_tools: web_extract,web_search')
            with patch.dict('os.environ', {'HERMES_REVIEW_PYTHON': '/configured/hermes/python',
                                           'INJECTED_SECRET': 'must-not-copy'}), patch('subprocess.run', side_effect=run) as transport:
                self.assertEqual(nr.run_codex(attempt, 'untrusted stdin prompt', timeout=12), '{"assistant":"final"}')
                self.assertEqual(transport.call_count, 1)
            command = nr.read_json(attempt / 'command.json')
            self.assertEqual(command['provider'], 'openai-codex')
            self.assertEqual(command['engine'], 'hermes-web-v1')
            self.assertEqual(command['allowed_tools'], ['web_extract', 'web_search'])
            self.assertIn('verified_tools', (attempt / 'engine.log').read_text())

    def test_policy_v2_schema_prompt_and_effective_model_cache_identity(self):
        nr = module()
        self.assertEqual(nr.POLICY['version'], 2)
        queue = nr.build_queue({'records': [PAIR]})
        self.assertIn(json.dumps(nr.RESULT_SCHEMA, ensure_ascii=False), nr.make_prompt(queue['candidates'][0]))
        with tempfile.TemporaryDirectory() as tmp:
            def response(attempt, *args):
                return json.dumps(result_for(nr.read_json(attempt / 'input.json')))
            with patch.object(nr, 'run_codex', side_effect=response) as engine:
                nr.review_queue(queue, tmp)
                summary = nr.review_queue(queue, tmp, model='gpt-6.1-sol')
                self.assertEqual(summary['skipped'], 1)
                self.assertEqual(engine.call_count, 1)

    def test_timeout_retains_logs_and_retryable_error(self):
        nr = module()
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict('os.environ', {'HERMES_REVIEW_PYTHON': '/configured/python'}), patch('subprocess.run', side_effect=subprocess.TimeoutExpired(['python'], 1, stderr=b'partial log')):
                summary = nr.review_queue(nr.build_queue({'records': [PAIR]}), tmp, timeout=1)
            self.assertEqual(summary['errors'], 1)
            self.assertFalse(list(Path(tmp).rglob('decision.json')))
            self.assertIn('partial log', next(Path(tmp).rglob('engine.log')).read_text())
            self.assertIn('timeout', next(Path(tmp).rglob('error.json')).read_text().lower())

    def test_public_cache_key_is_shared_with_automation(self):
        nr = module()
        candidate = nr.build_queue({'records': [PAIR]})['candidates'][0]
        expected = nr.digest({'candidate': candidate['fingerprint'], 'model': 'gpt-6.1-sol',
                              'engine': 'hermes-web-v1', 'input_result': None})
        self.assertEqual(nr.review_cache_key(candidate), expected)
        self.assertEqual(nr.review_cache_key(candidate, model='gpt-6.1-sol'), expected)
        self.assertNotEqual(nr.review_cache_key(candidate, input_result={}), expected)

    def test_missing_python_fails_closed_without_spawning_standalone_codex(self):
        nr = module()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict('os.environ', {}, clear=True), patch('subprocess.run') as transport:
                with self.assertRaisesRegex(RuntimeError, 'HERMES_REVIEW_PYTHON'):
                    nr.run_codex(Path(tmp), 'untrusted candidate', timeout=12)
                transport.assert_not_called()

class LegacyTransportTests(unittest.TestCase):
    def test_unconfigured_transport_records_retryable_failure_without_success_cache(self):
        nr = module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / 'queue.json'
            nr.write_json(queue, nr.build_queue({'records': [PAIR]}))
            with patch.dict('os.environ', {}, clear=True), patch('subprocess.run') as transport:
                for _ in range(2):
                    self.assertEqual(nr.main(['review', '--queue', str(queue),
                                             '--out-dir', str(root / 'reports')]), 1)
                transport.assert_not_called()
            self.assertFalse(list(root.rglob('decision.json')))
            self.assertEqual(len(list(root.rglob('error.json'))), 2)
            self.assertFalse(list(root.rglob('command.json')))
            self.assertFalse(list(root.rglob('raw-final.json')))



if __name__ == '__main__':
    unittest.main()
