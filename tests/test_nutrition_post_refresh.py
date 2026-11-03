import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PostRefreshTests(unittest.TestCase):
    def module(self):
        self.assertTrue((ROOT / 'nutrition_post_refresh.py').exists(), 'post-refresh helper is missing')
        spec = importlib.util.spec_from_file_location('post', ROOT / 'nutrition_post_refresh.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def committed_inputs(self, base):
        data = base / 'repo'
        data.mkdir()
        subprocess.run(['git', 'init', str(data)], check=True, capture_output=True)
        status = {'merge': {'status': 'merged'}, 'retailers': {'B': {'status': 'refreshed'}}}
        for name, value in [('refresh_status.json', json.dumps(status)),
                            ('nutrition_cache.json', '{}'), ('all_discounts.csv', 'product\ntomato\n')]:
            (data / name).write_text(value)
        subprocess.run(['git', '-C', str(data), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(data), '-c', 'user.name=Test', '-c', 'user.email=test@example.org',
                        'commit', '-m', 'inputs'], check=True, capture_output=True)
        return data, status

    def test_snapshot_committed_bytes_hashes_and_rejects_unpublished_data(self):
        post = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data, status = self.committed_inputs(base)
            manifest = post.create_snapshot(data, base / 'snapshot')
            self.assertEqual(manifest['schema_version'], 1)
            self.assertEqual(post.verify_snapshot(base / 'snapshot', status), manifest)
            (data / 'nutrition_cache.json').write_text('{"modified": true}')
            with self.assertRaisesRegex(ValueError, 'committed'):
                post.create_snapshot(data, base / 'unpublished')
            (base / 'snapshot' / 'all_discounts.csv').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'hash'):
                post.verify_snapshot(base / 'snapshot', status)

    def test_exact_snapshot_gate_binds_status_and_missing_snapshot_skips(self):
        post = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data, status = self.committed_inputs(base)
            post.create_snapshot(data, base / 'source')
            names = ['refresh-status', 'nutrition-review-inputs']
            def runner(cmd, **kwargs):
                if cmd[1] == 'api':
                    return subprocess.CompletedProcess(cmd, 0, json.dumps({'artifacts': [
                        {'id': i, 'name': name, 'expired': False} for i, name in enumerate(names)]}))
                target = Path(cmd[cmd.index('--dir') + 1])
                if cmd[cmd.index('--name') + 1] == 'refresh-status':
                    (target / 'refresh_status.json').write_text(json.dumps(status))
                else:
                    import shutil
                    shutil.copytree(base / 'source', target, dirs_exist_ok=True)
                return subprocess.CompletedProcess(cmd, 0)
            result = post.download_trigger('owner/repo', 123, base / 'out', runner)
            self.assertTrue(result['run'])
            self.assertEqual(Path(result['data_dir']), (base / 'out/inputs').resolve())
            self.assertEqual(len(result['artifacts']), 2)
            status['retailers']['B']['extra'] = 'different'
            with self.assertRaisesRegex(ValueError, 'status'):
                post.download_trigger('owner/repo', 123, base / 'mismatch', runner)
            names.pop()
            result = post.download_trigger('owner/repo', 123, base / 'missing', runner)
            self.assertFalse(result['run'])
            self.assertIn('immutable snapshot', result['reason'])

    def test_manifest_rejects_schema_commit_and_arbitrary_paths(self):
        post = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data, status = self.committed_inputs(base)
            original = post.create_snapshot(data, base / 'snapshot')
            for changes in ({'schema_version': 2}, {'source_commit': 'not-a-commit'},
                            {'sha256': {'../outside': '0' * 64}}):
                (base / 'snapshot/manifest.json').write_text(json.dumps({**original, **changes}))
                with self.assertRaisesRegex(ValueError, 'manifest'):
                    post.verify_snapshot(base / 'snapshot', status)

    def test_trigger_download_exact_run_and_no_artifact(self):
        post = self.module()
        calls = []
        def runner(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == 'api':
                return subprocess.CompletedProcess(cmd, 0, json.dumps({'artifacts': []}))
            raise AssertionError(cmd)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(post.download_trigger('owner/repo', '123', Path(tmp), runner))
        self.assertIn('repos/owner/repo/actions/runs/123/artifacts', calls[0])
        def with_artifact(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == 'api':
                return subprocess.CompletedProcess(cmd, 0, json.dumps([{'artifacts': [
                    {'id': 7, 'name': 'refresh-status', 'expired': False}]}]))
            (Path(cmd[cmd.index('--dir') + 1]) / 'refresh_status.json').write_text(
                json.dumps({'merge': {'status': 'merged'}, 'retailers': {'B': {'status': 'refreshed'}}}))
            return subprocess.CompletedProcess(cmd, 0)
        with tempfile.TemporaryDirectory() as tmp:
            result = post.download_trigger('owner/repo', '123', Path(tmp), with_artifact)
            self.assertTrue(post.should_review(result['trigger_status']))
            self.assertFalse(result['run'])
        self.assertEqual(calls[-1][1:4], ['run', 'download', '123'])
        self.assertIn('refresh-status', calls[-1])
        def broken(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)
        with self.assertRaises(subprocess.CalledProcessError):
            post.download_trigger('owner/repo', '123', Path('.'), broken)

    def test_snapshot_pipeline_uses_input_head_and_separate_code_checkout(self):
        post = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data, status = self.committed_inputs(base)
            manifest = post.create_snapshot(data, base / 'inputs')
            calls = []
            def runner(cmd, **kwargs):
                calls.append((cmd, kwargs))
                if 'scorer.py' in cmd:
                    (Path(cmd[cmd.index('--out-dir') + 1]) / 'matches.json').write_text('{"records": []}')
                    return subprocess.CompletedProcess(cmd, 0)
                return subprocess.run(cmd, **kwargs)
            result = post.run_pipeline(base / 'inputs', base / 'out', base / 'state',
                sys.executable, 'scorer.py', dry_run=True, runner=runner, code_dir=ROOT)
            self.assertEqual(result['inputs']['head'], manifest['source_commit'])
            self.assertFalse(any(cmd[0] == 'git' for cmd, _ in calls))
            scorer = next(cmd for cmd, _ in calls if 'scorer.py' in cmd)
            self.assertEqual(scorer[scorer.index('--data-dir') + 1], str(base / 'inputs'))
            self.assertEqual((base / 'inputs/all_discounts.csv').read_bytes(),
                             (data / 'all_discounts.csv').read_bytes())
            prepare = next(cmd for cmd, _ in calls if 'prepare' in cmd)
            self.assertEqual(prepare[1], str(ROOT / 'nutrition_review.py'))
            self.assertTrue(all(options['cwd'] == ROOT for _, options in calls))

    def test_pipeline_bounds_persists_and_dry_run_never_calls_codex(self):
        post = self.module()
        calls = []
        def runner(cmd, **kwargs):
            calls.append(cmd)
            if 'scorer.py' in cmd:
                out = Path(cmd[cmd.index('--out-dir') + 1])
                out.mkdir(parents=True, exist_ok=True)
                (out / 'matches.json').write_text(json.dumps({'records': [
                    {'id': str(i), 'prob': 0.2, 'product_text': 'tomato', 'source_product': 'raw tomato',
                     'source': 'test', 'source_url': 'https://example.org/tomato',
                     'values': {'Protein': {'value': 1, 'unit': 'g'}}} for i in range(5)]}))
                return subprocess.CompletedProcess(cmd, 0)
            return subprocess.run(cmd, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for output in ('out1', 'out2'):
                result = post.run_pipeline(ROOT, base / output, base / 'state',
                    sys.executable, 'scorer.py', limit=99, dry_run=True, runner=runner)
                self.assertEqual(result['attempted'], 3)
                self.assertEqual(result['pending_remaining'], 5)
                self.assertIn('input_fingerprint', result)
                self.assertIn('trigger-status.json', result['inputs'])
                self.assertTrue((base / output / 'queue.json').exists())
            self.assertTrue((base / 'state' / 'dry-run.json').exists())
            # Seed a syntactically validated persisted advisory decision, then verify resume.
            import nutrition_review
            queue = json.loads((base / 'out2' / 'queue.json').read_text())
            candidate = queue['candidates'][0]
            raw = {'candidate_id': candidate['candidate_id'], 'classification': 'insufficient_evidence',
                   'evidence': [], 'explanation': 'Offline test: no source retrieval', 'nutrition_values': []}
            key = nutrition_review.review_cache_key(candidate)
            nutrition_review.write_json(base / 'state' / key / 'decision.json',
                                        nutrition_review.validate_decision(raw, candidate))
            resumed = post.run_pipeline(ROOT, base / 'out3', base / 'state',
                sys.executable, 'scorer.py', dry_run=True, runner=runner)
            self.assertEqual(resumed['skipped'], 1)
            self.assertEqual(resumed['pending_remaining'], 4)
            self.assertTrue((base / 'out3' / 'reviews' / key / 'decision.json').exists())
        review_commands = [c for c in calls if 'review' in c]
        scorer_command = next(c for c in calls if 'scorer.py' in c)
        self.assertEqual(scorer_command[scorer_command.index('--subfolder') + 1], 'english')
        self.assertEqual(scorer_command[scorer_command.index('--lang') + 1], 'cs')
        self.assertEqual(scorer_command[scorer_command.index('--batch-size') + 1], '8')
        self.assertTrue(all('--dry-run' in c and c[c.index('--limit') + 1] == '3' for c in review_commands))

    def test_laya_failure_is_visible_and_never_reuses_old_queue(self):
        post = self.module()
        calls = []
        def runner(cmd, **kwargs):
            calls.append(cmd)
            if 'scorer.py' in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.run(cmd, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with self.assertRaises(subprocess.CalledProcessError):
                post.run_pipeline(ROOT, base / 'out', base / 'state',
                    sys.executable, 'scorer.py', runner=runner)
            summary = json.loads((base / 'out' / 'summary.json').read_text())
            self.assertIn('error', summary)
            self.assertFalse(any('review' in c for c in calls))

    def test_run_failure_replaces_successful_gate_summary(self):
        post = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'summary.json').write_text('{"run": true}')
            code = post.main(['run', '--data-dir', str(base), '--out-dir', str(base),
                              '--state-dir', str(base / 'state')])
            self.assertEqual(code, 1)
            self.assertIn('error', json.loads((base / 'summary.json').read_text()))

    def test_cli_transport_failure_is_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(ROOT / 'nutrition_post_refresh.py'),
                'download-trigger', '--repository', 'owner/repo', '--run-id', '123',
                '--out-dir', tmp], env={**__import__('os').environ, 'PATH': tmp},
                text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('error', result.stderr.lower())

    def test_workflow_is_trusted_independent_and_bounded(self):
        path = ROOT / '.github/workflows/nutrition-review.yml'
        self.assertTrue(path.exists(), 'independent workflow missing')
        text = path.read_text()
        self.assertIn("head_branch == 'main'", text)
        self.assertIn('head_repository.full_name == github.repository', text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn('workflow_run.id', text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertNotIn('needs:', text)
        self.assertNotIn('contents: write', text)
        self.assertIn('if: always()', text)
        self.assertIn('NUTRITION_REVIEW_LIMIT', text)
        self.assertIn('HERMES_REVIEW_PYTHON: ${{ vars.HERMES_REVIEW_PYTHON }}', text)
        self.assertIn('--code-dir "$GITHUB_WORKSPACE"', text)
        self.assertIn('DATA_DIR: ${{ steps.gate.outputs.data_dir }}', text)
        self.assertIn('--data-dir "$DATA_DIR"', text)

    def test_snapshot_and_manual_gate_cli_outputs(self):
        post = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data, _ = self.committed_inputs(base)
            self.assertEqual(post.main(['snapshot', '--data-dir', str(data), '--out-dir', str(base / 'snapshot')]), 0)
            output = base / 'github-output'
            self.assertEqual(post.main(['gate', '--status', str(data / 'refresh_status.json'),
                '--out-dir', str(base / 'gate'), '--github-output', str(output)]), 0)
            self.assertIn('data_dir=' + str(data.resolve()), output.read_text())

    def test_merge_gate_accepts_partial_failure_not_all_failed(self):
        gate = self.module().should_review
        self.assertTrue(gate({'merge': {'status': 'merged'}, 'retailers': {
            'A': {'status': 'failed'}, 'B': {'status': 'refreshed'}}}))
        self.assertFalse(gate({'merge': {'status': 'merged'}, 'retailers': {'A': {'status': 'failed'}}}))
        self.assertFalse(gate({'merge': {'status': 'failed'}, 'retailers': {'B': {'status': 'refreshed'}}}))


if __name__ == '__main__':
    unittest.main()
