import unittest

from build_site import dashboard_category


class DashboardCategoryTests(unittest.TestCase):
    def test_equivalent_native_categories(self):
        for store, source, target in (
            ("Albert", "Tržnice u Alberta", "Ovoce a zelenina"),
            ("Albert", "Albertovo pekařství", "Pečivo"),
            ("Tesco", "Pekárna", "Pečivo"),
        ):
            with self.subTest(store=store, source=source):
                self.assertEqual(dashboard_category(store, source), target)

    def test_broad_mixed_and_unknown_categories_are_not_guessed(self):
        for store, source in (
            ("Albert", "Trvanlivé"), ("Tesco", "Trvanlivé"),
            ("Albert", "Mléčné a chlazené"), ("Tesco", "Nápoje"),
            ("Albert", "Uzeniny a lahůdky"), ("Tesco", "Maso a lahůdky"),
            ("Unknown", "Tržnice u Alberta"), ("Billa", "Pečivo"),
        ):
            with self.subTest(store=store, source=source):
                self.assertEqual(dashboard_category(store, source), source)
