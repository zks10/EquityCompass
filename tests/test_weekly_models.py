import copy
from datetime import datetime
import json
from pathlib import Path
import unittest

from finance_news.weekly.models import (
    AnalysisStatus,
    DETECTOR_FAMILIES,
    DetectorFamily,
    DetectorName,
    DetectorResult,
    WeeklySnapshot,
)


FIXTURE = Path(__file__).parent / "fixtures" / "weekly" / "valid_opportunity_v1.json"


class DetectorContractTests(unittest.TestCase):
    def test_defines_exactly_six_frozen_detectors(self):
        self.assertEqual({item.value for item in DetectorName}, {
            "market_overreaction", "negative_news_resolution", "valuation_reset",
            "fundamental_inflection", "temporary_headwind", "emerging_catalyst",
        })

    def test_detector_families_match_frozen_methodology(self):
        self.assertIs(DETECTOR_FAMILIES[DetectorName.MARKET_OVERREACTION], DetectorFamily.MARKET_DISLOCATION)
        self.assertIs(DETECTOR_FAMILIES[DetectorName.VALUATION_RESET], DetectorFamily.MARKET_DISLOCATION)
        self.assertIs(DETECTOR_FAMILIES[DetectorName.NEGATIVE_NEWS_RESOLUTION], DetectorFamily.EVENT_EVOLUTION)
        self.assertIs(DETECTOR_FAMILIES[DetectorName.TEMPORARY_HEADWIND], DetectorFamily.EVENT_EVOLUTION)
        self.assertIs(DETECTOR_FAMILIES[DetectorName.FUNDAMENTAL_INFLECTION], DetectorFamily.BUSINESS_IMPROVEMENT)
        self.assertIs(DETECTOR_FAMILIES[DetectorName.EMERGING_CATALYST], DetectorFamily.FORWARD_CATALYST)

    def test_accepts_applicable_boundary_scores(self):
        for score in (0, 100):
            result = DetectorResult(DetectorName.MARKET_OVERREACTION, True, AnalysisStatus.COMPLETED, score)
            self.assertEqual(result.score, score)

    def test_accepts_non_applicable_null_score(self):
        result = DetectorResult(DetectorName.MARKET_OVERREACTION, False, AnalysisStatus.NOT_APPLICABLE, None)
        self.assertIsNone(result.score)

    def test_rejects_non_applicable_numeric_score(self):
        with self.assertRaises(ValueError):
            DetectorResult(DetectorName.MARKET_OVERREACTION, False, AnalysisStatus.NOT_APPLICABLE, 0)

    def test_rejects_completed_applicable_result_without_score(self):
        with self.assertRaises(ValueError):
            DetectorResult(DetectorName.MARKET_OVERREACTION, True, AnalysisStatus.COMPLETED, None)

    def test_rejects_failed_result_with_numeric_score(self):
        with self.assertRaises(ValueError):
            DetectorResult(DetectorName.MARKET_OVERREACTION, True, AnalysisStatus.FAILED, 10)

    def test_rejects_score_outside_range(self):
        with self.assertRaises(ValueError):
            DetectorResult(DetectorName.MARKET_OVERREACTION, True, AnalysisStatus.COMPLETED, 101)


class WeeklySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_round_trips_fictional_fixture(self):
        snapshot = WeeklySnapshot.from_dict(self.payload)
        self.assertEqual(snapshot.to_dict(), self.payload | {
            "opportunities": [self.payload["opportunities"][0] | {
                "ranking": self.payload["opportunities"][0]["ranking"] | {"final_score": 82.3}
            }]
        })

    def test_rejects_naive_publication_timestamp(self):
        snapshot = WeeklySnapshot.from_dict(self.payload)
        values = dict(snapshot.__dict__)
        values["published_at"] = datetime(2026, 8, 22, 12)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            WeeklySnapshot(**values)

    def test_rejects_more_than_five_opportunities(self):
        payload = copy.deepcopy(self.payload)
        original = payload["opportunities"][0]
        payload["opportunities"] = []
        for rank in range(1, 7):
            item = copy.deepcopy(original)
            item["rank"] = rank
            payload["opportunities"].append(item)
        with self.assertRaisesRegex(ValueError, "more than five"):
            WeeklySnapshot.from_dict(payload)

    def test_rejects_duplicate_ranks(self):
        payload = copy.deepcopy(self.payload)
        payload["opportunities"].append(copy.deepcopy(payload["opportunities"][0]))
        with self.assertRaisesRegex(ValueError, "unique"):
            WeeklySnapshot.from_dict(payload)

    def test_rejects_nonpositive_rank(self):
        payload = copy.deepcopy(self.payload)
        payload["opportunities"][0]["rank"] = 0
        with self.assertRaisesRegex(ValueError, "positive"):
            WeeklySnapshot.from_dict(payload)

    def test_rejects_cross_detector_score_outside_range(self):
        payload = copy.deepcopy(self.payload)
        payload["opportunities"][0]["cross_detector"]["freshness"] = 101
        with self.assertRaisesRegex(ValueError, "Freshness"):
            WeeklySnapshot.from_dict(payload)

    def test_preserves_evidence_contracts(self):
        serialized = WeeklySnapshot.from_dict(self.payload).to_dict()
        evidence = serialized["opportunities"][0]["evidence"]
        self.assertEqual(evidence[0]["role"], "primary")
        self.assertEqual(evidence[0]["fact_type"], "reported_fact")
        self.assertEqual(evidence[1]["fact_type"], "calculated_fact")

    def test_serializes_final_score_to_one_decimal(self):
        serialized = WeeklySnapshot.from_dict(self.payload).to_dict()
        self.assertEqual(serialized["opportunities"][0]["ranking"]["final_score"], 82.3)


if __name__ == "__main__":
    unittest.main()
