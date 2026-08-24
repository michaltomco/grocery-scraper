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
                history_row(),
                history_row(
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
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "index.html").write_text(build_site.build(), encoding="utf-8")

        handler = partial(QuietHandler, directory=str(site_dir))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

        cls.playwright = sync_playwright().start()
        cls.browser: Browser = cls.playwright.chromium.launch()

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
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []
        self.page.on("console", lambda message: self.console_errors.append(message.text) if message.type == "error" else None)
        self.page.on("pageerror", lambda exception: self.page_errors.append(str(exception)))

    def tearDown(self) -> None:
        self.context.close()

    def assert_browser_clean(self) -> None:
        self.assertEqual(self.console_errors, [], f"console errors: {self.console_errors}")
        self.assertEqual(self.page_errors, [], f"page errors: {self.page_errors}")

    def test_dashboard_theme_filter_ranking_and_date_controls(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        self.assertEqual(self.page.locator("#t tbody tr").count(), 1)
        self.assertEqual(self.page.locator('.ppcell[data-store="Albert"]').count(), 2)
        self.assertEqual(
            self.page.locator('.ppcell[data-store="Albert"]').evaluate_all(
                "cells => new Set(cells.map(cell => cell.dataset.line)).size"
            ),
            2,
        )

        self.page.get_by_role("button", name="Light").click()
        self.assertEqual(self.page.locator("html").get_attribute("data-theme"), "light")
        self.assertEqual(self.page.evaluate("localStorage.getItem('grocery-theme')"), "light")
        self.page.reload(wait_until="networkidle")
        self.assertEqual(self.page.locator("html").get_attribute("data-theme"), "light")

        tesco_chip = self.page.locator('.legend .chip[data-store="Tesco"]')
        tesco_chip.click()
        self.assertIn("off", tesco_chip.get_attribute("class") or "")
        self.assertEqual(
            self.page.locator('.ppcell[data-store="Tesco"].muted').count(),
            self.page.locator('.ppcell[data-store="Tesco"]').count(),
        )

        self.page.get_by_text("Rank by nutrient", exact=True).click()
        self.assertFalse(self.page.locator("#rankingCard").is_hidden())
        self.page.locator("#rankingCategory").select_option(index=0)
        self.page.locator("#rankingNutrient").select_option(index=0)
        self.assertGreater(self.page.locator("#rankingTable tbody tr").count(), 0)

        self.page.locator("#t .dcell .day").first.click()
        self.assertGreater(self.page.locator("#t .day.sel").count(), 0)
        self.assert_browser_clean()

    def test_product_detail_nutrition_mode_persists(self) -> None:
        self.page.goto(f"{self.base_url}/index.html", wait_until="networkidle")
        href = self.page.locator("#t a.product-link").first.get_attribute("href")
        self.assertIsNotNone(href)
        self.page.goto(f"{self.base_url}/{href}", wait_until="networkidle")

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


if __name__ == "__main__":
    unittest.main()
