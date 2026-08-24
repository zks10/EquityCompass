import copy
import json
from pathlib import Path
import unittest

from finance_news.weekly.config import (
    OpportunityConfigError,
    load_opportunity_config,
    parse_opportunity_config,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "phase3" / "opportunity-v1.json"


class WeeklyConfigTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_loads_frozen_configuration(self):
        config = load_opportunity_config(CONFIG_PATH)
        self.assertEqual(config.methodology_version, "phase-3.1-v1")
        self.assertEqual(config.eligibility.minimum_equity_score, 65)
        self.assertAlmostEqual(sum(config.ranking.weights), 1.0)

    def test_rejects_missing_section(self):
        payload = copy.deepcopy(self.payload)
        del payload["ranking"]
        with self.assertRaisesRegex(OpportunityConfigError, "missing"):
            parse_opportunity_config(payload)

    def test_rejects_unknown_field(self):
        payload = copy.deepcopy(self.payload)
        payload["ranking"]["mystery_weight"] = 0
        with self.assertRaisesRegex(OpportunityConfigError, "unknown"):
            parse_opportunity_config(payload)

    def test_rejects_weights_not_summing_to_one(self):
        payload = copy.deepcopy(self.payload)
        payload["ranking"]["primary_strength_weight"] = 0.5
        with self.assertRaisesRegex(OpportunityConfigError, "sum to 1.0"):
            parse_opportunity_config(payload)

    def test_rejects_threshold_outside_range(self):
        payload = copy.deepcopy(self.payload)
        payload["eligibility"]["minimum_final_rank"] = 101
        with self.assertRaisesRegex(OpportunityConfigError, "between 0 and 100"):
            parse_opportunity_config(payload)

    def test_rejects_descending_convergence_bonuses(self):
        payload = copy.deepcopy(self.payload)
        payload["supporting_signals"]["two_family_bonus"] = 2
        with self.assertRaisesRegex(OpportunityConfigError, "monotonically"):
            parse_opportunity_config(payload)

    def test_rejects_invalid_momentum_signs(self):
        payload = copy.deepcopy(self.payload)
        payload["thesis_momentum"]["weakening"] = 5
        with self.assertRaisesRegex(OpportunityConfigError, "positive, zero, and negative"):
            parse_opportunity_config(payload)

    def test_rejects_invalid_diversification_limit(self):
        payload = copy.deepcopy(self.payload)
        payload["diversification"]["maximum_opportunities"] = 6
        with self.assertRaisesRegex(OpportunityConfigError, "between 1 and 5"):
            parse_opportunity_config(payload)


if __name__ == "__main__":
    unittest.main()
