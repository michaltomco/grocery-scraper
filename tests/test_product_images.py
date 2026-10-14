"""Offline image provenance/cache regression tests."""
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from PIL import Image
import build_site


def png():
    out = io.BytesIO()
    Image.new('RGB', (30, 20), 'green').save(out, 'PNG')
    return out.getvalue()


class ProductImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patcher = patch.object(build_site, 'IMG_DIR', self.root)
        self.patcher.start()
        build_site.FAILED_IMAGES.clear()
        build_site.IMAGE_DIAGNOSTICS.clear()
        build_site.IMAGE_HOST_FAILURES.clear()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_store_url_identity_and_valid_cache_is_not_reencoded(self):
        with patch.object(build_site.IMAGE_OPENER, 'open', side_effect=lambda *a, **k: io.BytesIO(png())) as fetch:
            a = build_site.cache_image('same', 'https://cdn.test/a.png', 'Albert')
            b = build_site.cache_image('same', 'https://cdn.test/a.png', 'Tesco')
            c = build_site.cache_image('same', 'https://cdn.test/b.png', 'Albert')
            self.assertEqual(len({a, b, c}), 3)
            path = self.root / Path(a).name
            before = path.stat().st_mtime_ns
            self.assertEqual(build_site.cache_image('same', 'https://cdn.test/a.png', 'Albert'), a)
            self.assertEqual(path.stat().st_mtime_ns, before)
            self.assertEqual(fetch.call_count, 3)
            path.write_bytes(b'invalid image')
            self.assertEqual(build_site.cache_image('same', 'https://cdn.test/a.png', 'Albert'), a)
            self.assertEqual(fetch.call_count, 4)

    def test_kupi_is_never_downloaded_or_reused(self):
        (self.root / 'a.jpg').write_bytes(png())
        with patch.object(build_site.IMAGE_OPENER, 'open') as fetch:
            self.assertEqual(build_site.cache_image('a', 'https://img.kupi.cz/a.jpg', 'Billa'), 'img/veg.png')
            fetch.assert_not_called()

    def test_redirects_exclude_kupi_before_request_without_global_opener(self):
        from email.message import Message
        from urllib.response import addinfourl
        requested = []
        def transport(handler, request):
            requested.append(request.full_url)
            headers = Message()
            if request.full_url == 'http://retailer.test/image':
                headers['Location'] = 'http://img.kupi.cz/image.png'
                response = addinfourl(io.BytesIO(b''), headers, request.full_url, 302)
                response.msg = 'Found'
            else:
                response = addinfourl(io.BytesIO(png()), headers, request.full_url, 200)
                response.msg = 'OK'
            return response
        with patch.object(build_site.urllib.request.HTTPHandler, 'http_open', transport), patch.object(build_site.urllib.request, 'install_opener') as install:
            result = build_site.cache_image('redirect', 'http://retailer.test/image')
        self.assertEqual(requested, ['http://retailer.test/image'])
        self.assertEqual(result, 'img/veg.png')
        install.assert_not_called()

    def test_trailing_dot_kupi_initial_url_is_excluded_without_request(self):
        with patch.object(build_site.IMAGE_OPENER, 'open') as fetch:
            self.assertEqual(build_site.cache_image('dot', 'https://img.kupi.cz./a'), 'img/veg.png')
        fetch.assert_not_called()

    def test_trailing_dot_kupi_redirect_is_excluded_without_request(self):
        from email.message import Message
        from urllib.response import addinfourl
        requested = []
        def transport(handler, request):
            requested.append(request.full_url)
            headers = Message()
            headers['Location'] = 'http://IMG.KUPI.CZ./a'
            response = addinfourl(io.BytesIO(b''), headers, request.full_url, 302)
            response.msg = 'Found'
            return response
        with patch.object(build_site.urllib.request.HTTPHandler, 'http_open', transport):
            self.assertEqual(build_site.cache_image('dot', 'http://retailer.test/image'), 'img/veg.png')
        self.assertEqual(requested, ['http://retailer.test/image'])

    def test_malformed_image_url_falls_back_with_failure_diagnostic(self):
        with patch.object(build_site.IMAGE_OPENER, 'open') as fetch:
            self.assertEqual(build_site.cache_image('bad', 'https://[', 'Albert'), 'img/veg.png')
        fetch.assert_not_called()
        key = ('Albert', 'bad', 'https://[')
        self.assertIn(key, build_site.FAILED_IMAGES)
        self.assertIn('ValueError', build_site.IMAGE_DIAGNOSTICS[key])

    def test_percent_encoded_kupi_initial_url_is_excluded_without_request(self):
        with patch.object(build_site.IMAGE_OPENER, 'open', return_value=io.BytesIO(png())) as fetch:
            self.assertEqual(build_site.cache_image('encoded', 'https://img.%6bupi.cz/a'), 'img/veg.png')
        fetch.assert_not_called()

    def test_percent_encoded_kupi_redirect_is_excluded_without_request(self):
        from email.message import Message
        from urllib.response import addinfourl
        requested = []
        def transport(handler, request):
            requested.append(request.full_url)
            headers = Message()
            headers['Location'] = 'http://img.%6bupi.cz/a'
            response = addinfourl(io.BytesIO(b''), headers, request.full_url, 302)
            response.msg = 'Found'
            return response
        with patch.object(build_site.urllib.request.HTTPHandler, 'http_open', transport):
            self.assertEqual(build_site.cache_image('encoded', 'http://retailer.test/image'), 'img/veg.png')
        self.assertEqual(requested, ['http://retailer.test/image'])

    def test_allowed_cdn_redirect_is_followed(self):
        from email.message import Message
        from urllib.response import addinfourl
        requested = []
        def transport(handler, request):
            requested.append(request.full_url)
            headers = Message()
            if request.full_url == 'http://retailer.test/image':
                headers['Location'] = 'http://cdn.test/image.png'
                response = addinfourl(io.BytesIO(b''), headers, request.full_url, 302)
                response.msg = 'Found'
            else:
                response = addinfourl(io.BytesIO(png()), headers, request.full_url, 200)
                response.msg = 'OK'
            return response
        with patch.object(build_site.urllib.request.HTTPHandler, 'http_open', transport):
            result = build_site.cache_image('redirect', 'http://retailer.test/image')
        self.assertEqual(requested, ['http://retailer.test/image', 'http://cdn.test/image.png'])
        self.assertNotEqual(result, 'img/veg.png')

    def test_transport_outage_is_bounded_but_http_errors_do_not_block_host(self):
        import urllib.error
        build_site.IMAGE_HOST_FAILURES.clear()
        with patch.object(build_site.IMAGE_OPENER, 'open', side_effect=TimeoutError('offline')) as fetch:
            for i in range(20):
                self.assertEqual(build_site.cache_image(str(i), f'https://offline.test/{i}.jpg'), 'img/veg.png')
            self.assertLessEqual(fetch.call_count, 3)
        with patch.object(build_site.IMAGE_OPENER, 'open', side_effect=urllib.error.HTTPError('x', 404, 'missing', {}, None)) as fetch:
            for i in range(5):
                build_site.cache_image(str(i), f'https://missing.test/{i}.jpg')
            self.assertEqual(fetch.call_count, 5)

    def test_live_success_resets_consecutive_transport_failures(self):
        def fetch(req, **kwargs):
            if int(req.full_url.rsplit('/', 1)[1]) % 2 == 0:
                raise TimeoutError('intermittent')
            return io.BytesIO(png())
        with patch.object(build_site.IMAGE_OPENER, 'open', side_effect=fetch) as download:
            results = [build_site.cache_image(str(i), f'https://intermittent.test/{i}') for i in range(10)]
        self.assertEqual(download.call_count, 10)
        self.assertTrue(all(results[i] != 'img/veg.png' for i in range(1, 10, 2)))
        self.assertEqual(build_site.IMAGE_HOST_FAILURES['intermittent.test'], 0)

    def test_http_404_interrupts_consecutive_transport_failures(self):
        import urllib.error
        outcomes = [TimeoutError('offline'), urllib.error.HTTPError('x', 404, 'missing', {}, None)] * 2
        outcomes += [TimeoutError('offline'), io.BytesIO(png())]
        with patch.object(build_site.IMAGE_OPENER, 'open', side_effect=outcomes) as fetch:
            results = [build_site.cache_image(str(i), f'https://reachable.test/{i}') for i in range(6)]
        self.assertEqual(fetch.call_count, 6)
        self.assertEqual(results[:5], ['img/veg.png'] * 5)
        self.assertNotEqual(results[5], 'img/veg.png')
        self.assertEqual(build_site.IMAGE_HOST_FAILURES['reachable.test'], 0)

    def test_cache_hit_does_not_reset_transport_failures(self):
        with patch.object(build_site.IMAGE_OPENER, 'open', return_value=io.BytesIO(png())):
            cached = build_site.cache_image('cached', 'https://outage.test/cached')
        with patch.object(build_site.IMAGE_OPENER, 'open', side_effect=TimeoutError('offline')) as fetch:
            for i in range(2):
                build_site.cache_image(str(i), f'https://outage.test/{i}')
            self.assertEqual(build_site.cache_image('cached', 'https://outage.test/cached'), cached)
            self.assertEqual(build_site.IMAGE_HOST_FAILURES['outage.test'], 2)
            for i in range(2, 20):
                build_site.cache_image(str(i), f'https://outage.test/{i}')
        self.assertEqual(fetch.call_count, 3)

    def test_sustained_outage_with_eight_workers_is_bounded(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        barrier = threading.Barrier(8)
        lock = threading.Lock()
        calls = 0
        def fetch(req, **kwargs):
            nonlocal calls
            with lock:
                calls += 1
                first_batch = calls <= 8
            if first_batch:
                barrier.wait(timeout=10)
            raise TimeoutError('offline')
        with patch.object(build_site.IMAGE_OPENER, 'open', side_effect=fetch) as download, ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: build_site.cache_image(str(i), f'https://outage.test/{i}'), range(40)))
        self.assertEqual(results, ['img/veg.png'] * 40)
        self.assertLessEqual(download.call_count, 3 + 8 - 1)

    def test_grouped_pages_choose_decoded_image_with_visible_variant_label(self):
        entries = [dict(raw_name='Apple red', product_id='bad', image_url='https://cdn.test/bad', store='Albert', val=1, unit='kg'), dict(raw_name='Apple green', product_id='good', image_url='https://cdn.test/good', store='Tesco', val=2, unit='kg')]
        def fetch(req, **kwargs):
            return io.BytesIO(b'bad' if req.full_url.endswith('bad') else png())
        with patch.object(build_site.IMAGE_OPENER, 'open', side_effect=fetch), patch.object(build_site, 'PRODUCTS_DIR', self.root / 'products'):
            build_site.write_product_pages({'apple': entries}, {}, {})
        html = (self.root / 'products/apple.html').read_text()
        self.assertIn('direct-', html)
        self.assertIn('Representative image: Apple green · Tesco', html)

    def test_exact_pages_keep_store_sku_image_and_failure_diagnostics(self):
        rows = {
            'apple_albert': dict(raw_name='Apple', pretty='Apple', product_id='same', image_url='https://cdn.test/missing', store='Albert', val=1),
            'apple_tesco': dict(raw_name='Apple', pretty='Apple', product_id='same', image_url='https://cdn.test/good', store='Tesco', val=2),
        }
        def fetch(req, **kwargs):
            return io.BytesIO(b'invalid' if req.full_url.endswith('missing') else png())
        with patch.object(build_site.IMAGE_OPENER, 'open', side_effect=fetch), patch.object(build_site, 'PRODUCTS_EXACT_DIR', self.root / 'exact'):
            build_site.write_exact_product_pages({}, rows)
        self.assertIn('../../img/veg.png', (self.root / 'exact/apple_albert.html').read_text())
        self.assertNotIn('direct-', (self.root / 'exact/apple_albert.html').read_text())
        self.assertIn('direct-', (self.root / 'exact/apple_tesco.html').read_text())
        report = build_site.image_coverage_report()
        self.assertEqual(report['counts']['decoded-direct'], 1)
        self.assertEqual(report['counts']['failed'], 1)
        self.assertEqual(report['failures'][0]['store'], 'Albert')

    def test_build_prewarms_direct_candidates_and_resets_outage_state(self):
        from contextlib import ExitStack
        import threading
        from tests.test_frontend import history_row
        from scrapers.common import write_csv
        history = self.root / 'history.csv'
        write_csv(history, [history_row(image_url='https://outage.test/a.png')])
        build_site.IMAGE_HOST_FAILURES['outage.test'] = 100
        threads = []
        def fetch(req, **kwargs):
            threads.append(threading.current_thread().name)
            return io.BytesIO(png())
        with ExitStack() as stack:
            for name, value in {'HISTORY_CSV': history, 'SITE_DIR': self.root, 'PRODUCTS_DIR': self.root / 'products', 'PRODUCTS_EXACT_DIR': self.root / 'exact'}.items():
                stack.enter_context(patch.object(build_site, name, value))
            stack.enter_context(patch.object(build_site, 'get_many', return_value={}))
            stack.enter_context(patch.object(build_site, 'get_many_exact', return_value={}))
            stack.enter_context(patch.object(build_site.IMAGE_OPENER, 'open', side_effect=fetch))
            html = build_site.build()
        self.assertIn('direct-', html)
        self.assertEqual(len(threads), 1)
        self.assertNotEqual(threads[0], 'MainThread')

    def test_request_uses_truthful_urllib_client_identity(self):
        requests = []
        def fetch(request, **kwargs):
            requests.append(request)
            return io.BytesIO(png())
        with patch.object(build_site.IMAGE_OPENER, 'open', side_effect=fetch):
            self.assertNotEqual(build_site.cache_image('a', 'https://cdn.test/a.png'), 'img/veg.png')
        self.assertNotIn('Mozilla', requests[0].get_header('User-agent') or '')
        self.assertTrue((requests[0].get_header('User-agent') or '').startswith('Python-urllib/'))
