"""Offline reproducible nutrition audit; repairs ONLY proven contradictions.

uv run python audit_nutrition_integrity.py --report /path/audit.json
Add --quarantine to retain evidence in cache but hide contradictory values.
Lexical differences, proxy mappings and missing provenance are REVIEW flags,
not grounds for deleting nutrition. No research or source URLs are invented.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import nutrition


def audit_cache(cache: dict) -> dict:
    definite, review, quarantined = [], [], []
    status_counts = Counter()
    entries = [(key, value, False) for key, value in cache.items() if key != 'exact']
    entries += [(key, value, True) for key, value in cache.get('exact', {}).items()]
    for key, entry, exact in sorted(entries):
        status_counts[f"{'exact' if exact else 'grouped'}:{entry.get('status')}"] += 1
        if entry.get('status') == 'quarantined':
            quarantined.append({'key': key, 'issues': entry.get('validation_issues', [])})
            continue
        if entry.get('status') != 'found':
            continue
        label = key[6:] if exact else key.replace('_', ' ')
        issues = nutrition.identity_issues(label, entry) + nutrition.nutrient_issues(entry.get('values', {}))
        detail = {'key': key, 'source_product': entry.get('source_product'),
                  'source_url': entry.get('source_url'), 'issues': issues}
        if issues:
            definite.append(detail)
            continue
        flags = []
        if entry.get('values') and not any(v.get('value', 0) for v in entry['values'].values()):
            flags.append('zero_only_nutrition_requires_source_review')
        if not entry.get('source_url') or not entry.get('provenance'):
            flags.append('missing_source_url_or_provenance')
        if entry.get('provenance') == 'category_proxy':
            flags.append('category_proxy_requires_identity_review')
        if exact and nutrition.exact_name_tokens(label) != nutrition.exact_name_tokens(entry.get('source_product', '')):
            flags.append('nonidentical_exact_source_name_requires_review')
        if flags:
            review.append({**detail, 'issues': flags})
    return {'counts': {'entries': len(entries), 'definite_conflicts': len(definite),
                       'review_required': len(review), 'quarantined': len(quarantined)},
            'status_counts': dict(sorted(status_counts.items())),
            'definite_conflicts': definite, 'review_required': review,
            'quarantined': quarantined}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=nutrition.CACHE_PATH)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--quarantine', action='store_true')
    args = parser.parse_args()
    original = args.cache.read_bytes()
    cache = json.loads(original)
    report = audit_cache(cache)
    report['input_sha256'] = hashlib.sha256(original).hexdigest()
    if args.quarantine:
        for detail in report['definite_conflicts']:
            key = detail['key']
            namespace = cache['exact'] if key.startswith('exact:') else cache
            namespace[key] = nutrition.quarantine_entry(namespace[key], detail['issues'])
        # Match existing indentation/order; never touch CSV/site outputs.
        args.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        report['after_quarantine'] = audit_cache(cache)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['counts'], sort_keys=True))
    if 'after_quarantine' in report:
        print('After quarantine:', json.dumps(report['after_quarantine']['counts'], sort_keys=True))


if __name__ == '__main__':
    main()
