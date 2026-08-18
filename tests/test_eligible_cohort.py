import unittest

from finance_news.eligible_cohort import study_partition


class EligibleCohortTests(unittest.TestCase):
    def test_assigns_registered_time_partitions(self):
        self.assertEqual(study_partition("2021-12-31"), "development")
        self.assertEqual(study_partition("2022-01-01"), "validation")
        self.assertEqual(study_partition("2025-08-15"), "holdout")

    def test_rejects_date_outside_registered_study(self):
        with self.assertRaises(ValueError):
            study_partition("2026-01-01")


if __name__ == "__main__":
    unittest.main()
