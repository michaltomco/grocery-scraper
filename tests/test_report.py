"""Tests for the CSV price report."""

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import report
from scrapers.common import FIELDNAMES, read_csv, write_csv


def history_row(**overrides: object) -> dict[str, object]:
    today = date.today()
    value: dict[str, object] = {field: "" for field in FIELDNAMES}
    value.update(
        store="Albert",
        product_id="apple-1",
        product_name="Jablka 1 kg",
        canonical_product_name="jablka",
        price="29.9",
        currency="Kč",
        unit_price="29,90 Kč / 1 kg",
        price_per_kg="29.9",
        date_range=f"{today.isoformat()} - {(today + timedelta(days=2)).isoformat()}",
        url="https://example.test/apple",
        scraped_at=f"{today.isoformat()}T06:00:00+02:00",
    )
    value.update(overrides)
    return value


class PriceReportTests(unittest.TestCase):
    def test_describe_change_reports_direction_delta_and_percentage(self) -> None:
        self.assertEqual(report.describe_change(20.0, 25.0), ("dropped", -5.0, -20.0))
        self.assertEqual(report.describe_change(30.0, 25.0), ("increased", 5.0, 20.0))
        self.assertEqual(report.describe_change(25.0, None), ("unchanged", None, None))

    def test_main_reports_only_current_offers(self) -> None:
        today = date.today()
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            history = directory_path / "history.csv"
            output = directory_path / "price_report.csv"
            write_csv(
                history,
                [
                    history_row(),
                    history_row(
                        product_id="carrot-1",
                        product_name="Mrkev 1 kg",
                        canonical_product_name="mrkev",
                        date_range=f"{(today - timedelta(days=3)).isoformat()} - {(today - timedelta(days=1)).isoformat()}",
                    ),
                ],
            )
            with patch.object(report, "HISTORY_CSV", history), patch.object(report, "REPORT_CSV", output):
                report.main()

            rows = read_csv(output)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["canonical_product_name"], "jablka")
        self.assertEqual(rows[0]["best_store"], "Albert")
        self.assertEqual(rows[0]["best_price"], "29.90")


if __name__ == "__main__":
    unittest.main()
