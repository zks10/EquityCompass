import unittest

from finance_news.coverage_sensitivity import _coverage


class CoverageSensitivityTests(unittest.TestCase):
    def test_reports_completed_and_missing_price_coverage(self):
        rows = [{"status": "completed"}, {"status": "incomplete"}]
        self.assertEqual(
            _coverage(rows),
            {"attempted": 2, "completed": 1, "missing_initial_price": 1, "coverage_percent": 50.0},
        )


if __name__ == "__main__":
    unittest.main()
