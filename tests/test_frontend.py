"""Real-browser regression tests for the generated static dashboard.

These tests exercise the inline JavaScript in a real Chromium instance. They
build a temporary deterministic site fixture and serve it over localhost, so no
live Kupi, nutrition, or image service is contacted.
"""

from contextlib import ExitStack
from datetime import date, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

import build_site
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from scrapers.common import FIELDNAMES, write_csv


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def history_row(**overrides: object) -> dict[str, object]:
    today = date.today()
    value: dict[str, object] = {field: "" for field in FIELDNAMES}
    value.update(
        store="Albert",
        product_id="apple-1",
        product_name="Jablka Gala 1 kg",
        canonical_product_name="jablka",
        price="29.9",
        currency="Kč",
        unit_price="29,90 Kč / 1 kg",
        price_per_kg="29.9",
        date_range=f"{today.isoformat()} - {(today + timedelta(days=2)).isoformat()}",
        url="https://example.test/apple",
        image_url="https://example.test/apple.jpg",
        scraped_at=f"{today.isoformat()}T06:00:00+02:00",
    )
    value.update(overrides)
    return value


class DashboardBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = TemporaryDirectory()
        root = Path(cls.tempdir.name)
        history = root / "history.csv"
        site_dir = root / "site"
        products_dir = site_dir / "products"
        image_dir = site_dir / "img"
        image_dir.mkdir(parents=True)
        shutil.copyfile(build_site.ROOT / "veg.png", image_dir / "veg.png")
        write_csv(
            history,
            [
                history_row(category="Tržnice u Alberta"),
                history_row(
                    category="Tržnice u Alberta",
                    product_id="apple-1",
                    price="24.9",
                    unit_price="24,90 Kč / 1 kg",
                    price_per_kg="24.9",
                    date_range=f"{(date.today() + timedelta(days=3)).isoformat()} - {(date.today() + timedelta(days=5)).isoformat()}",
                    scraped_at=f"{date.today().isoformat()}T06:01:00+02:00",
                ),
                history_row(
                    store="Tesco",
                    product_id="apple-2",
                    price="34.9",
                    unit_price="34,90 Kč / 1 kg",
                    price_per_kg="34.9",
                ),
                history_row(
                    product_id="bread-1",
                    product_name="Chléb",
                    canonical_product_name="chleb",
                    category="Albertovo pekařství",
                    price="29.9",
                    unit_price="29,90 Kč / 1 kg",
                    price_per_kg="29.9",
                ),
                history_row(
                    store="Tesco",
                    product_id="bread-2",
                    product_name="Chléb",
                    canonical_product_name="chleb",
                    category="Pekárna",
                ),
            ],
        )
        nutrition = {
            "jablka": {
                "status": "found",
                "source": "fixture",
                "values": {
                    "Calories": {"value": 52, "unit": "kcal"},
                    "Carbs": {"value": 14, "unit": "g"},
                    "Fiber": {"value": 2.4, "unit": "g"},
                    "Vitamin C": {"value": 4.6, "unit": "mg"},
                },
            }
        }
        cls.patches = ExitStack()
        cls.patches.enter_context(patch.object(build_site, "HISTORY_CSV", history))
        cls.patches.enter_context(patch.object(build_site, "SITE_DIR", site_dir))
        cls.patches.enter_context(patch.object(build_site, "IMG_DIR", image_dir))
        cls.patches.enter_context(patch.object(build_site, "PRODUCTS_DIR", products_dir))
        cls.patches.enter_context(patch.object(build_site, "INDEX_HTML", site_dir / "index.html"))
        cls.patches.enter_context(patch.object(build_site, "get_many", return_value=nutrition))
        cls.patches.enter_context(patch.object(build_site, "cache_image", return_value="img/veg.png"))
        cls.patches.enter_context(patch.object(build_site, "get_many_exact", return_value={
            "exact:Jablka Gala 1 kg": {
                "status": "found",
                "source": "Open Food Facts",
                "source_product": "Apple Gala",
                "source_url": "https://example.test/off/123",
                "provenance": "exact_match",
                "values": {
                    "Calories": {"value": 52, "unit": "kcal"},
                    "Carbs": {"value": 14, "unit": "g"},
                    "Fiber": {"value": 2.4, "unit": "g"},
                    "Vitamin C": {"value": 4.6, "unit": "mg"},
                },
            },
            "exact:Chléb": {
                "status": "not_found",
                "query": "Chléb",
                "values": {},
            },
        }))
        products_exact_dir = site_dir / "products" / "exact"
        cls.patches.enter_context(patch.object(build_site, "PRODUCTS_EXACT_DIR", products_exact_dir))
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "index.html").write_text(build_site.build(), encoding="utf-8")

        handler = partial(QuietHandler, directory=str(site_dir))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

        cls.playwright = sync_playwright().start()
        # --no-sandbox: CI runners execute as root, where Chromium's sandbox
        # cannot start. --disable-dev-shm-usage avoids the small /dev/shm on
        # shared runners. Both are safe for local runs too.
        cls.browser: Browser = cls.playwright.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=5)
        cls.patches.close()
        cls.tempdir.cleanup()

    def setUp(self) -> None:
        self.context: BrowserContext = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.page: Page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []
        self.page.on("console", lambda message: self.console_errors.append(message.text) if message.type == "error" else None)
        self.page.on("pageerror", lambda exception: self.page_errors.append(str(exception)))

    def tearDown(self) -> None:
        self.context.close()

    def assert_browser_clean(self) -> None:
        self.assertEqual(self.console_errors, [], f"console errors: {self.console_errors}")
        self.assertEqual(self.page_errors, [], f"page errors: {self.page_errors}")

    def test_exact_modes_handle_corrupt_storage_and_prices_do_not_filter(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until='networkidle')
        self.page.evaluate("localStorage.setItem('grocery-nutrition-mode', '\"[broken')")
        self.page.goto(f"{self.base_url}/products/exact/jablka_gala_1_kg__albert.html", wait_until='networkidle')
        self.assertEqual(self.page.locator('#nutritionMode [data-mode="axis"]').get_attribute('aria-pressed'), 'true')
        self.page.get_by_role('button', name='% daily intake', exact=True).click()
        self.assertEqual(self.page.locator('.mode-rda').first.evaluate('el => getComputedStyle(el).display'), 'inline')
        self.page.locator('.discount-price').click()
        self.assertEqual(self.page.locator('#discountTable .muted').count(), 0)
        self.page.goto(f"{self.base_url}/products/jablka.html", wait_until='networkidle')
        self.page.locator('.discount-price').first.click()
        self.assertEqual(self.page.locator('#discountTable .muted').count(), 0)
        self.assert_browser_clean()

    def test_appearance_switch_clears_preloaded_theme_styles(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until='networkidle')
        self.page.get_by_role('combobox', name='Appearance', exact=True).select_option('dark')
        self.page.reload(wait_until='networkidle')
        self.page.get_by_role('combobox', name='Appearance', exact=True).select_option('light')
        self.assertEqual(self.page.locator('html').evaluate('el => getComputedStyle(el).backgroundColor'), 'rgb(239, 241, 245)')
        self.assertEqual(self.page.locator('html').evaluate('el => getComputedStyle(el).colorScheme'), 'light')
        self.page.get_by_role('combobox', name='Appearance', exact=True).select_option('system')
        self.assertEqual(self.page.locator('html').evaluate('el => el.style.backgroundColor'), '')
        self.assertEqual(self.page.locator('html').evaluate('el => el.style.colorScheme'), 'light dark')
        self.assert_browser_clean()

    def test_appearance_header_and_disclosure_indicator(self) -> None:
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.goto(f"{self.base_url}/index.html", wait_until='networkidle')
        self.assertEqual(self.page.get_by_role('combobox', name='Appearance', exact=True).count(), 1)
        self.assertEqual(self.page.locator('#filtersToggle .disclosure').inner_text(), '▸')
        self.page.locator('#filtersToggle').click()
        self.assertEqual(self.page.locator('#filtersToggle .disclosure').inner_text(), '▾')
        self.assert_browser_clean()

    def test_detail_modes_are_plain_language_and_unavailable_modes_hidden(self) -> None:
        self.page.goto(f"{self.base_url}/products/jablka.html", wait_until='networkidle')
        self.assertEqual(self.page.get_by_role('button', name='Bars', exact=True).count(), 1)
        self.page.get_by_role('button', name='% daily intake', exact=True).click()
        self.assertEqual(self.page.get_by_role('button', name='% daily intake', exact=True).get_attribute('aria-pressed'), 'true')
        self.assertIn('recommended daily intake', self.page.locator('#dailyIntakeHelp').inner_text())
        self.page.get_by_role('button', name='Amounts', exact=True).click()
        self.page.reload(wait_until='networkidle')
        self.assertEqual(self.page.get_by_role('button', name='Amounts', exact=True).get_attribute('aria-pressed'), 'true')
        self.page.goto(f"{self.base_url}/products/exact/chléb__albert.html", wait_until='networkidle')
        self.assertEqual(self.page.locator('#nutritionMode:visible').count(), 0)
        self.assert_browser_clean()

    def test_mobile_filters_appearance_and_touch_targets(self) -> None:
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.goto(f"{self.base_url}/index.html", wait_until='networkidle')
        disclosure = self.page.locator('#filtersToggle')
        self.assertEqual(disclosure.get_attribute('aria-expanded'), 'false')
        disclosure.click()
        self.assertEqual(disclosure.get_attribute('aria-expanded'), 'true')
        self.assertEqual(self.page.get_by_role('combobox', name='Appearance', exact=True).count(), 1)
        self.page.get_by_role('combobox', name='Appearance', exact=True).select_option('light')
        self.page.get_by_role('checkbox', name='With nutrition data').check()
        self.assertIn('1', disclosure.inner_text())
        self.page.get_by_role('button', name='Reset filters', exact=True).click()
        self.assertEqual(self.page.locator('html').get_attribute('data-theme'), 'light')
        self.page.reload(wait_until='networkidle')
        disclosure.click()
        self.assertEqual(self.page.get_by_role('combobox', name='Appearance', exact=True).input_value(), 'light')
        metrics = self.page.evaluate('''() => ({width: innerWidth, body: document.body.scrollWidth, toolbar: document.querySelector('.filter-controls').scrollWidth, controls: document.querySelector('.controls').scrollWidth})''')
        self.assertLessEqual(metrics['body'], metrics['width'], metrics)
        self.assertLessEqual(metrics['toolbar'], metrics['width'], metrics)
        self.assertLessEqual(metrics['controls'], metrics['width'], metrics)
        category = self.page.locator('.category-filter').bounding_box()
        search = self.page.locator('.search-filter').bounding_box()
        self.assertGreater(search['y'], category['y'])
        self.assertGreaterEqual(search['width'], 340)
        for selector in ['.legend .chip', '#rankToggle', '.date-presets button', '#filtersToggle', '#appearance']:
            for box in self.page.locator(selector).evaluate_all('els => els.filter(el => el.getClientRects().length).map(el => el.getBoundingClientRect().height)'):
                self.assertGreaterEqual(box, 44, selector)
        self.assert_browser_clean()

    def test_date_presets_summary_custom_bounds_and_reset(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        self.assertEqual(self.page.get_by_role('button', name='Today', exact=True).count(), 1)
        self.page.get_by_role('button', name='Today', exact=True).click()
        start = self.page.locator('#rangeStart').input_value()
        self.assertEqual(start, self.page.locator('#rangeEnd').input_value())
        self.assertIn(start, self.page.locator('#filterSummary').inner_text())
        self.assertIn('1 matching product', self.page.locator('#filterSummary').inner_text())
        self.page.get_by_role('button', name='Next 7 days', exact=True).click()
        self.assertEqual(self.page.evaluate("JSON.parse(localStorage.getItem('grocery-filters')).rangeEndOffset"), 6)
        self.page.get_by_role('button', name='Choose range', exact=True).click()
        self.assertTrue(self.page.locator('#rangeStart').is_visible())
        self.assertEqual(self.page.locator('#rangeStart').get_attribute('min'), start)
        self.page.locator('#rangeStart').fill(self.page.locator('#rangeEnd').get_attribute('max'))
        self.assertEqual(self.page.locator('#rangeStart').input_value(), self.page.locator('#rangeEnd').input_value())
        self.assertIn('0 matching products', self.page.locator('#filterSummary').inner_text())
        self.page.get_by_role('button', name='Albert', exact=True).click()
        self.page.get_by_role('button', name='Tesco', exact=True).click()
        self.page.get_by_role('button', name='Full window', exact=True).click()
        self.assertIn('0 matching products', self.page.locator('#filterSummary').inner_text())
        self.page.locator('#categoryFilter').select_option(label='Pečivo')
        self.page.locator('#productSearch').fill('nothing')
        self.page.get_by_role('checkbox', name='With nutrition data').check()
        self.page.get_by_role('button', name='Hide', exact=True).click()
        self.page.get_by_role('button', name='Best nutrient value', exact=True).click()
        self.page.get_by_role('button', name='Reset filters', exact=True).click()
        self.assertEqual(self.page.locator('#categoryFilter').input_value(), 'Ovoce a zelenina')
        self.assertEqual(self.page.locator('#productSearch').input_value(), '')
        self.assertFalse(self.page.locator('#nutritionFilter').is_checked())
        self.assertEqual(self.page.locator('#dimToggle').get_attribute('aria-pressed'), 'true')
        self.assertTrue(self.page.locator('#rankingCard').is_hidden())
        self.assertEqual(self.page.locator('.legend [aria-pressed="true"]').count(), 4)
        self.assertIn('1 matching product', self.page.locator('#filterSummary').inner_text())
        self.page.reload(wait_until='networkidle')
        self.assertIn('1 matching product', self.page.locator('#filterSummary').inner_text())
        self.assert_browser_clean()

    def test_semantic_nonmatching_nutrition_and_ranking_controls(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        self.assertEqual(self.page.get_by_role('button', name='Dim', exact=True).count(), 1)
        self.assertEqual(self.page.get_by_role('button', name='Dim', exact=True).get_attribute('aria-pressed'), 'true')
        self.page.get_by_role('button', name='Hide', exact=True).click()
        self.assertEqual(self.page.get_by_role('button', name='Hide', exact=True).get_attribute('aria-pressed'), 'true')
        rank = self.page.get_by_role('button', name='Best nutrient value', exact=True)
        before = self.page.locator('#t .pname').all_text_contents()
        rank.click()
        self.assertEqual(rank.get_attribute('aria-expanded'), 'true')
        self.assertEqual(self.page.locator('#t .pname').all_text_contents(), before)
        self.page.locator('#categoryFilter').select_option(label='Pečivo')
        self.page.get_by_role('checkbox', name='With nutrition data').check()
        self.assertEqual(self.page.locator('#t tbody tr:visible').count(), 0)
        self.page.reload(wait_until='networkidle')
        self.assertTrue(self.page.get_by_role('checkbox', name='With nutrition data').is_checked())
        self.assertEqual(rank.get_attribute('aria-expanded'), 'true')
        self.assertEqual(self.page.locator('#t tbody tr:visible').count(), 0)
        self.assert_browser_clean()

    def test_store_buttons_toggle_inclusion_and_prices_are_informational(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        store = self.page.get_by_role("button", name="Albert", exact=True)
        self.assertEqual(store.get_attribute("aria-pressed"), "true")
        store.focus()
        self.page.keyboard.press("Space")
        self.assertEqual(store.get_attribute("aria-pressed"), "false")
        self.assertEqual(self.page.locator('.legend .chip[data-store="Tesco"]').get_attribute("aria-pressed"), "true")
        self.page.locator('#t .ppcell[data-store="Albert"]:visible').first.click()
        self.assertEqual(store.get_attribute("aria-pressed"), "false")
        store.click()
        self.assertEqual(store.get_attribute("aria-pressed"), "true")
        self.assert_browser_clean()

    def test_dashboard_theme_filter_ranking_and_date_controls(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        self.assertEqual(self.page.locator("#t tbody tr").count(), 2)
        apple_row = self.page.locator('#t tbody tr:has(a[href="products/jablka.html"])')
        self.assertEqual(apple_row.locator('.ppcell[data-store="Albert"]').count(), 2)
        self.assertEqual(
            apple_row.locator('.ppcell[data-store="Albert"]').evaluate_all(
                "cells => new Set(cells.map(cell => cell.dataset.line)).size"
            ),
            2,
        )

        self.page.get_by_role("combobox", name="Appearance").select_option("light")
        self.assertEqual(self.page.locator("html").get_attribute("data-theme"), "light")
        self.assertEqual(self.page.evaluate("localStorage.getItem('grocery-theme')"), "light")
        self.page.reload(wait_until="networkidle")
        self.assertEqual(self.page.locator("html").get_attribute("data-theme"), "light")

        tesco_chip = self.page.locator('.legend .chip[data-store="Tesco"]')
        tesco_chip.click()
        self.assertIn("off", tesco_chip.get_attribute("class") or "")
        self.assertNotIn("off", self.page.locator('.legend .chip[data-store="Albert"]').get_attribute("class") or "")
        self.assertEqual(
            self.page.locator('.ppcell[data-store="Tesco"].muted').count(),
            self.page.locator('.ppcell[data-store="Tesco"]').count(),
        )

        self.page.get_by_role("button", name="Best nutrient value", exact=True).click()
        self.assertFalse(self.page.locator("#rankingCard").is_hidden())
        self.page.locator("#rankingCategory").select_option(index=0)
        self.page.locator("#rankingNutrient").select_option(index=0)
        self.assertGreater(self.page.locator("#rankingTable tbody tr").count(), 0)
        self.assertEqual(
            self.page.locator("#rankingTable .ranking-product span").all_text_contents(),
            ["Jablka Gala 1 kg", "Jablka Gala 1 kg", "Jablka Gala 1 kg"],
        )

        self.page.locator("#t .dcell .day:visible").first.click()
        self.assertGreater(self.page.locator("#t .day.sel").count(), 0)
        self.assert_browser_clean()

    def test_filters_survive_a_page_reload(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")

        self.page.get_by_role("checkbox", name="With nutrition data").click()
        self.page.get_by_role("button", name="Hide", exact=True).click()
        self.page.get_by_role("button", name="Best nutrient value", exact=True).click()
        self.page.locator('.legend .chip[data-store="Tesco"]').click()

        self.page.reload(wait_until="networkidle")

        self.assertTrue(self.page.locator("#nutritionFilter").is_checked())
        self.assertEqual(self.page.locator("#hideToggle").get_attribute("aria-pressed"), "true")
        self.assertIn("on", self.page.get_by_role("button", name="Best nutrient value", exact=True).get_attribute("class") or "")
        self.assert_browser_clean()

    def test_main_date_column_keeps_the_full_timeline_visible(self) -> None:
        # 900px is the dashboard's maximum desktop width, where all four table
        # columns must coexist without collapsing the two-week timeline.
        self.page.set_viewport_size({"width": 900, "height": 1000})
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        metrics = self.page.locator("#t tbody .dcell").first.evaluate(
            """cell => {
                const timeline = cell.querySelector('.timeline');
                const cellBox = cell.getBoundingClientRect();
                const timelineBox = timeline.getBoundingClientRect();
                return {
                    cellWidth: cellBox.width,
                    timelineWidth: timelineBox.width,
                    timelineRight: timelineBox.right,
                    cellRight: cellBox.right,
                    overflowing: timeline.scrollWidth > cell.clientWidth,
                };
            }"""
        )
        self.assertFalse(metrics["overflowing"], metrics)
        self.assertLessEqual(metrics["timelineRight"], metrics["cellRight"] + 4, metrics)

        mobile = self.browser.new_context(viewport={"width": 390, "height": 844})
        page = mobile.new_page()
        errors: list[str] = []
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda exception: errors.append(str(exception)))
        page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        mobile_metrics = page.locator("#t tbody .dcell").first.evaluate(
            """cell => {
                const timeline = cell.querySelector('.timeline');
                const wrap = document.querySelector('.table-scroll');
                return {
                    clipped: timeline.scrollWidth > cell.clientWidth,
                    scrollable: wrap.scrollWidth > wrap.clientWidth,
                };
            }"""
        )
        self.assertFalse(mobile_metrics["clipped"], mobile_metrics)
        self.assertTrue(mobile_metrics["scrollable"], mobile_metrics)
        self.assertEqual(errors, [])
        mobile.close()
        self.assert_browser_clean()

    def test_product_search_filters_and_persists(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        search = self.page.locator("#productSearch")
        search.fill("jablka")
        self.page.wait_for_timeout(250)
        apples = self.page.locator('#t tbody tr:has(a[href="products/jablka.html"])')
        bread = self.page.locator('#t tbody tr:has(a[href="products/chleb.html"])')
        self.assertEqual(apples.evaluate("row => getComputedStyle(row).display"), "table-row")
        self.assertEqual(bread.evaluate("row => getComputedStyle(row).display"), "none")
        self.page.reload(wait_until="networkidle")
        self.assertEqual(self.page.locator("#productSearch").input_value(), "jablka")
        self.assertEqual(bread.evaluate("row => getComputedStyle(row).display"), "none")
        self.assert_browser_clean()

    def test_produce_category_includes_native_albert_labels_and_ranking(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        self.assertEqual(self.page.locator("#categoryFilter").input_value(), "Ovoce a zelenina")
        albert = self.page.locator('#t .ppcell[data-store="Albert"]:visible')
        self.assertEqual(albert.count(), 2, "Albert produce must not vanish under its retailer category name")
        self.assertEqual(self.page.locator('#t .dcell[data-store="Albert"]:visible').count(), 2)
        self.page.get_by_role("button", name="Best nutrient value", exact=True).click()
        self.assertEqual(self.page.locator('#rankingTable tbody tr').count(), 3)
        self.assert_browser_clean()

    def test_saved_retailer_category_restores_to_shared_category(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        for source, target, shown, hidden in (
            ("Albertovo pekařství", "Pečivo", "chleb", "jablka"),
            ("Pekárna", "Pečivo", "chleb", "jablka"),
            ("Tržnice u Alberta", "Ovoce a zelenina", "jablka", "chleb"),
        ):
            with self.subTest(source=source):
                self.page.evaluate("category => localStorage.setItem('grocery-filters', JSON.stringify({category}))", source)
                self.page.reload(wait_until="networkidle")
                self.assertEqual(self.page.locator("#categoryFilter").input_value(), target)
                self.assertEqual(self.page.evaluate("JSON.parse(localStorage.getItem('grocery-filters')).category"), target)
                self.assertTrue(self.page.locator(f'#t a[href="products/{shown}.html"]').is_visible())
                self.assertFalse(self.page.locator(f'#t a[href="products/{hidden}.html"]').is_visible())
        self.assert_browser_clean()

    def test_category_dropdown_filters_offer_lines(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        self.assertEqual(self.page.locator("#categoryFilter").count(), 1)
        self.page.locator("#categoryFilter").select_option(label="Pečivo")
        bread = self.page.locator('#t tbody tr:has(a[href="products/chleb.html"])')
        apples = self.page.locator('#t tbody tr:has(a[href="products/jablka.html"])')
        self.assertEqual(bread.evaluate("row => getComputedStyle(row).display"), "table-row")
        self.assertEqual(bread.locator('.ppcell:visible').count(), 2)
        self.assertEqual(bread.locator('.dcell:visible').count(), 2)
        self.assertEqual(bread.locator('.ppcell:visible').evaluate_all("cells => cells.map(c => c.dataset.sourceCategory).sort()"),
                         ["Albertovo pekařství", "Pekárna"])
        self.assertEqual(apples.evaluate("row => getComputedStyle(row).display"), "none")
        self.page.locator("#categoryFilter").select_option(label="Ovoce a zelenina")
        self.assertEqual(apples.evaluate("row => getComputedStyle(row).display"), "table-row")
        self.assert_browser_clean()

    def test_nutrition_filter_shows_only_products_with_nutrition_data(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        apples = self.page.locator('#t tbody tr:has(a[href="products/jablka.html"])')
        bread = self.page.locator('#t tbody tr:has(a[href="products/chleb.html"])')

        self.page.get_by_role("checkbox", name="With nutrition data").click()
        self.assertTrue(self.page.locator("#nutritionFilter").is_checked())
        self.assertIn("nutrition-only", self.page.locator("#t").get_attribute("class") or "")
        self.assertEqual(apples.evaluate("row => getComputedStyle(row).display"), "table-row")
        self.assertEqual(bread.evaluate("row => getComputedStyle(row).display"), "none")

        self.page.get_by_role("checkbox", name="With nutrition data").click()
        self.assertFalse(self.page.locator("#nutritionFilter").is_checked())
        self.assertNotIn("nutrition-only", self.page.locator("#t").get_attribute("class") or "")
        self.assertEqual(apples.evaluate("row => getComputedStyle(row).display"), "table-row")
        self.assert_browser_clean()

    def test_product_detail_nutrition_mode_persists(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        href = self.page.locator('#t a.product-link[href="products/jablka.html"]').get_attribute("href")
        self.assertIsNotNone(href)
        self.page.goto(f"{self.base_url}/{href}", wait_until="networkidle")
        self.assertEqual(
            self.page.locator("#discountTable thead th").all_text_contents(),
            ["Name", "Store", "Price", "Discount days"],
        )
        self.assertEqual(
            self.page.locator("#discountTable tbody .discount-produce").all_text_contents(),
            ["Jablka Gala 1 kg", "Jablka Gala 1 kg", "Jablka Gala 1 kg"],
        )

        self.page.locator('#nutritionMode [data-mode="rda"]').click()
        self.assertEqual(
            self.page.locator("#nutritionMode button.active").get_attribute("data-mode"),
            "rda",
        )
        self.assertEqual(
            self.page.locator(".mode-rda").first.evaluate("element => getComputedStyle(element).display"),
            "inline",
        )
        self.assertEqual(self.page.evaluate("localStorage.getItem('grocery-nutrition-mode')"), "rda")
        self.assert_browser_clean()

    def test_exact_variant_page_shows_provenance_and_unavailable_state(self) -> None:
        # Grouped page links each exact variant to its own page.
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        href = self.page.locator('#t a.product-link[href="products/jablka.html"]').get_attribute("href")
        assert href is not None
        self.page.goto(f"{self.base_url}/{href}", wait_until="networkidle")
        self.assertIsNotNone(self.page.locator('a.exact-link[href="exact/jablka_gala_1_kg__albert.html"]').first.get_attribute("href"))

        # Exact variant page shows the exact manufacturer source link.
        self.page.goto(f"{self.base_url}/products/exact/jablka_gala_1_kg__albert.html", wait_until="networkidle")
        self.assertEqual(
            self.page.locator("p.muted").filter(has_text="Source:").all_inner_texts(),
            ["Source: Open Food Facts — Apple Gala · exact entry"],
        )
        self.assertEqual(
            self.page.locator('a[href="https://example.test/off/123"]').all_inner_texts(),
            ["Apple Gala"],
        )

        # Unmatched SKU page shows the provenance-correct unavailable state (no fallback).
        self.page.goto(f"{self.base_url}/products/exact/chléb__albert.html", wait_until="networkidle")
        self.assertIn(
            "Nutrition data unavailable",
            self.page.locator("p.muted").filter(has_text="Nutrition data unavailable").inner_text(),
        )
        self.assert_browser_clean()


if __name__ == "__main__":
    unittest.main()
