"""Deterministic HTTP/Chromium coverage for deferred image loading."""
from contextlib import ExitStack
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from PIL import Image
from playwright.sync_api import sync_playwright, expect
import build_site
from scrapers.common import write_csv
from tests.test_frontend import QuietHandler, history_row


class ImageLoadingBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.site = root / 'site'
        (cls.site / 'img').mkdir(parents=True)
        rows = []
        for i in range(70):
            label = 'Fixture ' + chr(97 + i // 26) + chr(97 + i % 26)
            rows.append(history_row(product_id=f'apple-{i}', product_name=label, canonical_product_name=f'apple_{i}', category='Tržnice u Alberta'))
        rows.append(history_row(product_id='bread', product_name='Chléb', canonical_product_name='chleb', category='Albertovo pekařství'))
        write_csv(root / 'history.csv', rows)
        def image(pid, url, store=''):
            path = cls.site / 'img' / f'{pid}.png'
            Image.new('RGB', (40, 40), 'green').save(path)
            return f'img/{pid}.png'
        nutrition = {build_site.canonical_product_name(r['product_name']): {'status': 'found', 'values': {'Fiber': {'value': 2, 'unit': 'g'}}} for r in rows}
        with ExitStack() as stack:
            for name, value in {'HISTORY_CSV': root / 'history.csv', 'SITE_DIR': cls.site, 'IMG_DIR': cls.site / 'img', 'PRODUCTS_DIR': cls.site / 'products', 'PRODUCTS_EXACT_DIR': cls.site / 'products/exact'}.items():
                stack.enter_context(patch.object(build_site, name, value))
            stack.enter_context(patch.object(build_site, 'cache_image', side_effect=image))
            stack.enter_context(patch.object(build_site, 'get_many', return_value=nutrition))
            stack.enter_context(patch.object(build_site, 'get_many_exact', return_value={}))
            (cls.site / 'index.html').write_text(build_site.build())
        Image.new('RGB', (40, 40), 'white').save(cls.site / 'img/veg.png')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(cls.site)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=['--no-sandbox', '--disable-dev-shm-usage'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def test_search_matches_product_names_not_image_annotations(self):
        context = self.browser.new_context(viewport={'width': 1440, 'height': 800})
        self.addCleanup(context.close)
        page = context.new_page()
        console, errors = [], []
        page.on('console', lambda m: console.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{self.server.server_port}/', wait_until='networkidle')
        page.locator('#categoryFilter').select_option(label='Ovoce a zelenina')
        self.assertIn('Representative image:', page.locator('#t .pname').first.inner_text())
        for query, count in [('Representative image', 0), ('Albert', 0), ('Fixture', 70), ('Chléb', 1)]:
            with self.subTest(query=query):
                page.locator('#productSearch').fill('')
                page.wait_for_function("!document.querySelector('#t tbody tr.search-hidden')")
                page.locator('#categoryFilter').select_option(label='Pečivo' if query == 'Chléb' else 'Ovoce a zelenina')
                page.locator('#productSearch').fill(query)
                expect(page.locator('#t tbody tr:visible')).to_have_count(count)
        self.assertEqual(console, [])
        self.assertEqual(errors, [])

    def test_hidden_categories_scroll_search_and_ranking_are_deferred(self):
        context = self.browser.new_context(viewport={'width': 1440, 'height': 800})
        self.addCleanup(context.close)
        page = context.new_page()
        requested, console, errors, failures = [], [], [], []
        page.on('request', lambda r: requested.append(r.url))
        page.on('console', lambda m: console.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('requestfailed', lambda r: failures.append(r.failure))
        page.goto(f'http://127.0.0.1:{self.server.server_port}/', wait_until='networkidle')
        self.assertFalse(any('/bread.png' in u for u in requested))
        self.assertFalse(any('/apple-69.png' in u for u in requested))
        last = page.locator('#t tbody tr:visible').last
        last.scroll_into_view_if_needed()
        page.wait_for_function("document.querySelector('img[data-src=\"img/apple-69.png\"]').naturalWidth > 0")
        self.assertTrue(any('/apple-69.png' in u for u in requested))
        page.locator('#categoryFilter').select_option(label='Pečivo')
        page.locator('#t tbody tr:visible').first.scroll_into_view_if_needed()
        page.wait_for_function("document.querySelector('img[data-src=\"img/bread.png\"]').naturalWidth > 0")
        self.assertEqual(page.locator('#t tbody tr:visible').count(), 1)
        page.locator('#productSearch').fill('no match')
        expect(page.locator("#t tbody tr:visible")).to_have_count(0)
        page.locator('#productSearch').fill('Chléb')
        expect(page.locator('#t tbody tr:visible')).to_have_count(1)
        page.get_by_text('Rank by nutrient', exact=True).click()
        page.locator('#rankingTable img').first.scroll_into_view_if_needed()
        page.wait_for_function("document.querySelector('#rankingTable img').naturalWidth > 0")
        self.assertEqual(page.locator('#rankingTable img').first.get_attribute('decoding'), 'async')
        # A corrupt 200 image tests client fallback without deliberate HTTP errors.
        (self.site / 'img/broken.png').write_bytes(b'not an image')
        page.evaluate("() => { const i=document.querySelector('#rankingTable img'); i.dataset.src='img/broken.png'; i.removeAttribute('src'); }")
        page.evaluate('window.dispatchEvent(new Event("scroll"))')
        page.wait_for_function("document.querySelector('#rankingTable img').getAttribute('src') === 'img/veg.png' && document.querySelector('#rankingTable img').naturalWidth > 0")
        self.assertEqual(console, [])
        self.assertEqual(errors, [])
        self.assertEqual(failures, [])
