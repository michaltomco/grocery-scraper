"""Tests for Kupi's product-history graph parser."""

import unittest

from kupi_history import parse_kupi_graph_html


GRAPH_HTML = '''
<div id="graph"></div>
<script>
var graph_data = {
  "low": "[1779919200000, 22.9, https:\\/\\/img.kupi.cz\\/shop-a.png], [1780005600000, 19.9, https:\\/\\/img.kupi.cz\\/shop-b.jpg]",
  "avg": "[1779919200000, 23.9,  ], [1780005600000, 20.9,  ]",
  "bef": "[1779919200000, 39.9,  ], [1780005600000, 39.9,  ]",
  "unit": "1 kg"
};
</script>
'''


class KupiHistoryParserTests(unittest.TestCase):
    def test_parses_all_daily_price_series_in_prague_dates(self) -> None:
        rows = parse_kupi_graph_html(
            GRAPH_HTML,
            product_id="2",
            canonical_product_name="banany",
            product_name="Banány",
        )

        self.assertEqual(len(rows), 6)
        self.assertEqual(
            rows[0],
            {
                "product_id": "2",
                "canonical_product_name": "banany",
                "product_name": "Banány",
                "observed_date": "2026-05-28",
                "series": "lowest_discount",
                "price": 22.9,
                "currency": "Kč",
                "unit": "1 kg",
                "store_logo_url": "https://img.kupi.cz/shop-a.png",
                "source": "kupi_graph",
            },
        )
        self.assertEqual(rows[1]["observed_date"], "2026-05-29")
        self.assertEqual(rows[2]["series"], "average_discount")
        self.assertEqual(rows[4]["series"], "regular_price")

    def test_returns_no_rows_when_graph_payload_is_missing(self) -> None:
        self.assertEqual(
            parse_kupi_graph_html("<html><body>No graph</body></html>", "2", "banany", "Banány"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
