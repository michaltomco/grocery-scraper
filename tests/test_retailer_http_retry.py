"""Offline transport regressions: no live retailer requests or real sleeps."""
import io
from contextlib import redirect_stderr
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests
from scrapers import albert, tesco


def response(status=200, payload=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result.url = 'https://retailer.example/catalogue'
    result.headers.update(headers or {})
    result.json = Mock(return_value=payload if payload is not None else {'ok': True})
    return result


class RetailerHttpRetryTests(unittest.TestCase):
    def test_albert_transport_failure_retries_with_backoff_and_diagnostics(self):
        for error in (requests.ConnectTimeout('connect'), requests.ReadTimeout('read'),
                      requests.ConnectionError('reset')):
            with self.subTest(error=type(error).__name__):
                diagnostics = io.StringIO()
                with patch.object(albert.requests, 'get', side_effect=[error, error, response()]) as get, patch('time.sleep') as sleep, redirect_stderr(diagnostics):
                    self.assertEqual(albert.fetch_json(albert.STORES_URL), {'ok': True})
                self.assertEqual(get.call_count, 3)
                self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])
                self.assertEqual(get.call_args.kwargs['timeout'], 30)
                self.assertIn('Albert', diagnostics.getvalue())
                self.assertIn(type(error).__name__, diagnostics.getvalue())
                self.assertIn('attempt 1/4', diagnostics.getvalue())

    def test_tesco_transient_status_retries_identical_read_only_post(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                session = Mock(spec=requests.Session)
                session.post.side_effect = [response(status), response(payload={'data': {'category': {}}})]
                with patch('time.sleep') as sleep, redirect_stderr(io.StringIO()) as diagnostics:
                    self.assertEqual(tesco.query(session, 'GetCategoryProducts', tesco.PRODUCT_QUERY, {'page': 2}), {'category': {}})
                self.assertEqual(session.post.call_count, 2)
                self.assertEqual(session.post.call_args_list[0], session.post.call_args_list[1])
                self.assertEqual(session.post.call_args.kwargs['timeout'], 45)
                sleep.assert_called_once_with(2)
                self.assertIn(f'HTTP {status}', diagnostics.getvalue())
                self.assertIn('GetCategoryProducts', diagnostics.getvalue())

    def test_retry_after_seconds_and_http_dates_are_honored(self):
        for header, expected in [('7', 7), ('Thu, 01 Jan 1970 00:16:49 GMT', 9),
                                 ('Thu, 01 Jan 1970 00:16:30 GMT', 2),
                                 ('invalid', 2), ('-5', 2)]:
            with self.subTest(header=header):
                with patch.object(albert.requests, 'get', side_effect=[response(429, headers={'Retry-After': header}), response()]), patch('time.sleep') as sleep, patch('time.time', return_value=1000), redirect_stderr(io.StringIO()):
                    albert.fetch_json(albert.STORES_URL)
                sleep.assert_called_once_with(expected)

    def test_retry_delay_cap_and_total_budget_fail_without_early_retry(self):
        for header, attempts, delays in [('3600', 1, []), ('25', 3, [25, 25])]:
            with self.subTest(header=header):
                session = Mock(spec=requests.Session)
                session.post.side_effect = lambda *a, **kw: response(429, headers={'Retry-After': header})
                diagnostics = io.StringIO()
                with patch('time.sleep') as sleep, redirect_stderr(diagnostics):
                    with self.assertRaises(requests.HTTPError):
                        tesco.query(session, 'GetCategoryProducts', tesco.PRODUCT_QUERY, {'page': 3})
                self.assertEqual(session.post.call_count, attempts)
                self.assertEqual([c.args[0] for c in sleep.call_args_list], delays)
                self.assertIn('retry delay budget exhausted', diagnostics.getvalue())

    def test_tesco_paces_category_pages_including_department_boundaries(self):
        departments = [{'name': name, 'id': name} for name in sorted(tesco.FOOD_DEPARTMENTS)]
        session = Mock(spec=requests.Session)
        session.headers = {}
        def send(url, **kwargs):
            if url.endswith('GetTaxonomy'):
                return response(payload={'data': {'taxonomy': departments}})
            variables = kwargs['json']['variables']
            if variables['facet'] == departments[0]['id']:
                items = [{'id': str(variables['page'])}]
                total = 2
            else:
                items, total = [], 0
            return response(payload={'data': {'category': {'info': {'total': total, 'page': variables['page'], 'count': len(items)}, 'products': items}}})
        session.post.side_effect = send
        context = Mock()
        context.__enter__ = Mock(return_value=session)
        context.__exit__ = Mock(return_value=False)
        with patch.object(tesco.requests, 'Session', return_value=context), patch.object(tesco, 'extract_tesco_offers', return_value=[]), patch('time.sleep') as sleep:
            tesco.fetch_tesco_offers()
        self.assertEqual(sleep.call_count, len(departments) + 1)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1] * (len(departments) + 1))

    def test_tesco_rejects_non_catalogue_documents_before_post(self):
        session = Mock(spec=requests.Session)
        with self.assertRaisesRegex(ValueError, 'read-only'):
            tesco.query(session, 'GetCategoryProducts', 'mutation { updateBasket }', {})
        session.post.assert_not_called()

    def test_nonretryable_statuses_fail_immediately(self):
        for status in (400, 401, 403, 404, 422, 501):
            with self.subTest(status=status):
                session = Mock(spec=requests.Session)
                session.post.return_value = response(status)
                with patch('time.sleep') as sleep:
                    with self.assertRaises(requests.HTTPError):
                        tesco.query(session, 'GetTaxonomy', tesco.TAXONOMY_QUERY, {})
                session.post.assert_called_once()
                sleep.assert_not_called()
                session.post.return_value.json.assert_not_called()

    def test_json_and_schema_failures_are_not_retried(self):
        for retailer in (albert, tesco):
            for payload in ([], {'errors': ['invalid']}, {'status': 429}, {'data': {}}):
                # Albert accepts any dict at the transport boundary; its store
                # catalogue validator must still fail rather than retry it.
                result = response(payload=payload)
                session = Mock(spec=requests.Session)
                session.post.return_value = result
                with self.subTest(retailer=retailer.__name__, payload=payload), patch.object(albert.requests, 'get', return_value=result) as get, patch('time.sleep') as sleep:
                    with self.assertRaises((ValueError, AttributeError)):
                        if retailer is albert:
                            albert.fetch_albert_stores()
                        else:
                            tesco.query(session, 'GetTaxonomy', tesco.TAXONOMY_QUERY, {})
                    (get if retailer is albert else session.post).assert_called_once()
                    sleep.assert_not_called()
            result = response()
            result.json.side_effect = requests.exceptions.JSONDecodeError('invalid', 'x', 0)
            session = Mock(spec=requests.Session)
            session.post.return_value = result
            with patch.object(albert.requests, 'get', return_value=result) as get, patch('time.sleep') as sleep:
                with self.assertRaises(requests.exceptions.JSONDecodeError):
                    if retailer is albert:
                        albert.fetch_json(albert.STORES_URL)
                    else:
                        tesco.query(session, 'GetTaxonomy', tesco.TAXONOMY_QUERY, {})
                (get if retailer is albert else session.post).assert_called_once()
                sleep.assert_not_called()

    def test_exhaustion_preserves_snapshot_and_history_for_both_retailers(self):
        for retailer in (albert, tesco):
            with self.subTest(retailer=retailer.__name__), TemporaryDirectory() as directory:
                snapshot = Path(directory) / 'store.csv'
                history = Path(directory) / 'history.csv'
                snapshot.write_bytes(b'previous snapshot\n')
                history.write_bytes(b'previous history\n')
                session = Mock(spec=requests.Session)
                session.headers = {}
                session.post.return_value = response(429)
                context = Mock()
                context.__enter__ = Mock(return_value=session)
                context.__exit__ = Mock(return_value=False)
                diagnostics = io.StringIO()
                with patch.object(albert.requests, 'get', side_effect=requests.ConnectTimeout('connect')) as get, patch.object(tesco.requests, 'Session', return_value=context), patch('time.sleep') as sleep, redirect_stderr(diagnostics):
                    with self.assertRaises(requests.RequestException):
                        retailer.main(snapshot, history)
                self.assertEqual((get if retailer is albert else session.post).call_count, 4)
                self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4, 8])
                self.assertIn('retries exhausted at attempt 4/4', diagnostics.getvalue())
                self.assertEqual(snapshot.read_bytes(), b'previous snapshot\n')
                self.assertEqual(history.read_bytes(), b'previous history\n')

    def test_tesco_later_page_exhaustion_never_publishes_partial_catalogue(self):
        departments = [{'name': name, 'id': name} for name in sorted(tesco.FOOD_DEPARTMENTS)]
        session = Mock(spec=requests.Session)
        session.headers = {}
        session.post.side_effect = [
            response(payload={'data': {'taxonomy': departments}}),
            response(payload={'data': {'category': {'info': {'total': 2, 'page': 1, 'count': 1}, 'products': [{'id': 'first-page'}]}}}),
            *[response(429) for _ in range(4)],
        ]
        context = Mock()
        context.__enter__ = Mock(return_value=session)
        context.__exit__ = Mock(return_value=False)
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / 'tesco.csv'
            history = Path(directory) / 'history.csv'
            snapshot.write_text('old snapshot')
            history.write_text('old history')
            with patch.object(tesco.requests, 'Session', return_value=context), patch.object(tesco, 'extract_tesco_offers') as extract, patch('time.sleep'), redirect_stderr(io.StringIO()):
                with self.assertRaises(requests.HTTPError):
                    tesco.main(snapshot, history)
                extract.assert_not_called()
            self.assertEqual(session.post.call_count, 6)
            self.assertEqual(session.post.call_args.kwargs['json']['variables']['page'], 2)
            self.assertEqual(snapshot.read_text(), 'old snapshot')
            self.assertEqual(history.read_text(), 'old history')


if __name__ == '__main__':
    unittest.main()
