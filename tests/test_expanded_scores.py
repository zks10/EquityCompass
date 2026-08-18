import unittest

from finance_news.expanded_scores import historical_ticker


class ExpandedScoresTests(unittest.TestCase):
    def test_uses_verified_historical_common_ticker(self):
        record = {"eligible_security_classes": [{"ticker": " old ", "exchange": "NYSE"}]}
        self.assertEqual(historical_ticker(record), "OLD")

    def test_rejects_missing_verified_ticker(self):
        with self.assertRaises(ValueError):
            historical_ticker({"eligible_security_classes": []})


if __name__ == "__main__":
    unittest.main()
