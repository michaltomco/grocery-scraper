"""Independent, advisory post-extraction nutrition automation (never publishes data)."""

import argparse
import fcntl
import hashlib
import os
import shutil
import sys
import json
from pathlib import Path
import subprocess
import tempfile
import re


INPUT_FILES = ('refresh_status.json', 'nutrition_cache.json', 'all_discounts.csv')


def create_snapshot(data_dir, out_dir):
    """Freeze only bytes present in the committed data HEAD, never dirty output."""
    data_dir, out_dir = Path(data_dir).resolve(), Path(out_dir)
    head = subprocess.run(['git', '-C', str(data_dir), 'rev-parse', 'HEAD'],
                          check=True, capture_output=True, text=True).stdout.strip()
    contents = {}
    for name in INPUT_FILES:
        committed = subprocess.run(['git', '-C', str(data_dir), 'show', f'{head}:{name}'],
                                   check=True, capture_output=True).stdout
        if not (data_dir / name).is_file() or (data_dir / name).read_bytes() != committed:
            raise ValueError(f'input differs from committed data: {name}')
        contents[name] = committed
    manifest = {'schema_version': 1, 'source_commit': head,
                'sha256': {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}}
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, value in contents.items():
        (out_dir / name).write_bytes(value)
    (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def verify_snapshot(data_dir, status=None):
    data_dir = Path(data_dir)
    manifest = json.loads((data_dir / 'manifest.json').read_text())
    if (manifest.get('schema_version') != 1
            or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', str(manifest.get('source_commit', '')))
            or set(manifest.get('sha256', {})) != set(INPUT_FILES)):
        raise ValueError('invalid snapshot manifest schema, source commit or filenames')
    for name in INPUT_FILES:
        if hashlib.sha256((data_dir / name).read_bytes()).hexdigest() != manifest['sha256'][name]:
            raise ValueError(f'snapshot hash mismatch: {name}')
    if status is not None and json.loads((data_dir / 'refresh_status.json').read_text()) != status:
        raise ValueError('snapshot status differs from triggering status')
    return manifest


def download_trigger(repository, run_id, out_dir, runner=subprocess.run):
    """Resolve only the triggering run; an API/download error must stay an error."""
    if not str(run_id).isdigit() or int(run_id) <= 0:
        raise ValueError('run id must be positive numeric')
    response = runner(['gh', 'api', '--paginate', '--slurp',
                       f'repos/{repository}/actions/runs/{run_id}/artifacts'],
                      check=True, capture_output=True, text=True, timeout=120, shell=False)
    pages = json.loads(response.stdout)
    if isinstance(pages, dict):
        pages = [pages]
    all_artifacts = [a for page in pages for a in page['artifacts']]
    artifacts = [a for a in all_artifacts if a['name'] == 'refresh-status']
    if not artifacts:
        return None
    if len(artifacts) != 1 or artifacts[0].get('expired'):
        raise ValueError('ambiguous or expired refresh-status artifact')
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='trigger-', dir=out_dir) as temporary:
        runner(['gh', 'run', 'download', str(run_id), '--repo', repository,
                '--name', 'refresh-status', '--dir', temporary],
               check=True, capture_output=True, text=True, timeout=120, shell=False)
        status = json.loads((Path(temporary) / 'refresh_status.json').read_text())
    (out_dir / 'trigger-status.json').write_text(json.dumps(status, indent=2) + '\n')
    result = {'run': False, 'trigger_status': status, 'artifacts': artifacts,
              'reason': 'no successfully merged extraction', 'data_dir': ''}
    if should_review(status):
        snapshots = [a for a in all_artifacts if a['name'] == 'nutrition-review-inputs']
        if not snapshots:
            result['reason'] = 'unpublished data: no immutable snapshot'
            return result
        if len(snapshots) != 1 or snapshots[0].get('expired'):
            raise ValueError('ambiguous or expired nutrition-review-inputs artifact')
        inputs = (out_dir / 'inputs').resolve()
        # Never let a prior run's files complete a partial download.
        if inputs.exists():
            shutil.rmtree(inputs)
        inputs.mkdir()
        runner(['gh', 'run', 'download', str(run_id), '--repo', repository,
                '--name', 'nutrition-review-inputs', '--dir', str(inputs)],
               check=True, capture_output=True, text=True, timeout=120, shell=False)
        result.update(run=True, reason='merged extraction with immutable snapshot',
                      data_dir=str(inputs), manifest=verify_snapshot(inputs, status),
                      artifacts=artifacts + snapshots)
    return result


def run_pipeline(data_dir, out_dir, state_dir, laya_python, scorer, limit=3,
                 dry_run=False, runner=subprocess.run, code_dir=None):
    data_dir, out_dir, state_dir = map(lambda p: Path(p).resolve(), (data_dir, out_dir, state_dir))
    code_dir = Path(code_dir or Path(__file__).resolve().parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    limit = max(0, min(3, int(limit)))
    summary = {'approval_allowed': False, 'limit': limit, 'dry_run': dry_run,
               'limitation': 'Cached English checkpoint on Czech inputs; unvalidated identity triage, not nutrition verification.'}
    if (data_dir / 'manifest.json').exists():
        trigger = out_dir / 'trigger-status.json'
        status = json.loads(trigger.read_text()) if trigger.exists() else None
        head = verify_snapshot(data_dir, status)['source_commit']
    else:
        head = runner(['git', '-C', str(data_dir), 'rev-parse', 'HEAD'],
                      check=True, capture_output=True, text=True, timeout=30, shell=False).stdout.strip()
    inputs = {'head': head}
    for name in ('refresh_status.json', 'nutrition_cache.json', 'all_discounts.csv'):
        path = data_dir / name
        inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    trigger = out_dir / 'trigger-status.json'
    inputs['trigger-status.json'] = hashlib.sha256(trigger.read_bytes()).hexdigest() if trigger.exists() else None
    summary['inputs'] = inputs
    summary['input_fingerprint'] = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    # Nonblocking lock also protects local manual invocations, not just Actions concurrency.
    with (state_dir / '.lock').open('a') as lock, (out_dir / 'automation.log').open('a') as log:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def execute(command, timeout):
            log.write(json.dumps(command) + '\n')
            log.flush()
            return runner(command, check=True, stdout=log, stderr=subprocess.STDOUT,
                          timeout=timeout, shell=False, cwd=code_dir)
        try:
            # Unique directory prevents reusing stale matches if a failed scorer leaves files behind.
            with tempfile.TemporaryDirectory(prefix='laya-', dir=out_dir) as scores:
                execute([str(laya_python), str(scorer), '--data-dir', str(data_dir),
                         '--out-dir', scores, '--subfolder', 'english', '--lang', 'cs',
                         '--device', 'cpu', '--batch-size', '8'], 1800)
                shutil.copytree(scores, out_dir / 'laya', dirs_exist_ok=True)
                execute([sys.executable, str(code_dir / 'nutrition_review.py'), 'prepare',
                         '--scores', str(Path(scores) / 'matches.json'), '--out-dir', str(out_dir)], 120)
            command = [sys.executable, str(code_dir / 'nutrition_review.py'), 'review',
                       '--queue', str(out_dir / 'queue.json'), '--out-dir', str(state_dir),
                       '--limit', str(limit), '--timeout', '180']
            if dry_run:
                command.append('--dry-run')
            # Review CLI writes its report even if individual investigations fail.
            failure = None
            try:
                execute(command, 660)
            except subprocess.CalledProcessError as exc:
                failure = exc
            report_name = 'dry-run.json' if dry_run else 'report.json'
            report = json.loads((state_dir / report_name).read_text())
            summary.update(report)
            summary['pending_remaining'] = report['candidates'] - report['skipped'] - report['reviewed']
            shutil.copy2(state_dir / report_name, out_dir / 'review-result.json')
            # Include only this queue's persisted attempts; never upload unrelated runner state.
            queue = json.loads((out_dir / 'queue.json').read_text())
            import nutrition_review
            for candidate in queue['candidates']:
                key = nutrition_review.review_cache_key(candidate)
                if (state_dir / key).is_dir():
                    shutil.copytree(state_dir / key, out_dir / 'reviews' / key, dirs_exist_ok=True)
            if failure:
                raise failure
        except Exception as exc:
            summary['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            (out_dir / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def should_review(status):
    return (status.get('merge', {}).get('status') == 'merged'
            and any(item.get('status') == 'refreshed'
                    for item in status.get('retailers', {}).values()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    snapshot = sub.add_parser('snapshot', help='Freeze committed published input bytes')
    snapshot.add_argument('--data-dir', type=Path, required=True)
    snapshot.add_argument('--out-dir', type=Path, default=Path('nutrition-review-inputs'))
    gate = sub.add_parser('download-trigger', help='Gate using exactly one upstream run artifact')
    gate.add_argument('--repository', required=True)
    gate.add_argument('--run-id', required=True)
    gate.add_argument('--out-dir', type=Path, default=Path('nutrition-review-output'))
    gate.add_argument('--github-output', type=Path)
    manual = sub.add_parser('gate', help='Manual: gate using latest committed refresh status')
    manual.add_argument('--status', type=Path, default=Path('refresh_status.json'))
    manual.add_argument('--out-dir', type=Path, default=Path('nutrition-review-output'))
    manual.add_argument('--github-output', type=Path)
    run = sub.add_parser('run', help='Local cached Laya, prepare, bounded persistent review')
    run.add_argument('--data-dir', type=Path, default=Path.cwd())
    run.add_argument('--code-dir', type=Path, default=Path(__file__).resolve().parent)
    run.add_argument('--out-dir', type=Path, default=Path('nutrition-review-output'))
    run.add_argument('--state-dir', type=Path, default=Path(os.environ.get('NUTRITION_REVIEW_STATE_DIR') or Path.home() / '.local/share/grocery-nutrition-review'))
    run.add_argument('--laya-python', default=os.environ.get('LAYA_PYTHON') or str(Path.home() / 'Projects/laya-nutrition-correlation/.venv/bin/python'))
    run.add_argument('--scorer', default=os.environ.get('LAYA_SCORER') or str(Path.home() / '.hermes/skills/laya-record-correlation-scoring/scripts/laya_record_correlation.py'))
    run.add_argument('--limit', type=int, default=int(os.environ.get('NUTRITION_REVIEW_LIMIT') or 3))
    run.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / 'automation.log').touch()
    try:
        if args.command == 'snapshot':
            result = create_snapshot(args.data_dir, args.out_dir)
        elif args.command == 'run':
            result = run_pipeline(args.data_dir, args.out_dir, args.state_dir, args.laya_python,
                                  args.scorer, args.limit, args.dry_run, code_dir=args.code_dir)
        else:
            if args.command == 'download-trigger':
                result = download_trigger(args.repository, args.run_id, args.out_dir)
                if result is None:
                    result = {'run': False, 'reason': 'no refresh-status artifact', 'data_dir': ''}
                result['trigger_run_id'] = args.run_id
            else:
                status = json.loads(args.status.read_text())
                allowed = should_review(status)
                result = {'run': allowed, 'reason': 'manual committed extraction' if allowed else 'no merged extraction',
                          'trigger_status': status, 'data_dir': str(args.status.resolve().parent)}
            allowed = result['run']
            if args.github_output:
                with args.github_output.open('a') as output:
                    output.write('run=' + str(allowed).lower() + '\n')
                    output.write('data_dir=' + result['data_dir'] + '\n')
            (args.out_dir / 'gate.json').write_text(json.dumps(result, indent=2) + '\n')
            # Every run, including intentional skips, has an artifact summary.
            (args.out_dir / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        error = f'nutrition automation error: {type(exc).__name__}: {exc}'
        print(error, file=sys.stderr)
        with (args.out_dir / 'automation.log').open('a') as log:
            log.write(error + '\n')
        summary_path = args.out_dir / 'summary.json'
        existing = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        existing['error'] = error
        summary_path.write_text(json.dumps(existing, indent=2) + '\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
