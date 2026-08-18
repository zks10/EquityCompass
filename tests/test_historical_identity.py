"""Tests for filing-time security identity resolution."""

from __future__ import annotations

import unittest

from finance_news.historical_identity import (
    classify_security_classes,
    extract_security_classes,
)


class HistoricalIdentityTests(unittest.TestCase):
    def test_extracts_security_class_from_legacy_xbrl_instance(self):
        xml = """<xbrl xmlns:dei="http://xbrl.sec.gov/dei/2014-01-31">
          <dei:Security12bTitle contextRef="class">Common Stock</dei:Security12bTitle>
          <dei:TradingSymbol contextRef="class">TEST</dei:TradingSymbol>
          <dei:SecurityExchangeName contextRef="class">NYSE</dei:SecurityExchangeName>
        </xbrl>"""
        self.assertEqual(extract_security_classes(xml)[0]["ticker"], "TEST")

    def test_extracts_and_accepts_common_stock_class(self) -> None:
        html = """
        <ix:nonNumeric name="dei:Security12bTitle" contextRef="class-a">Class A Common Stock</ix:nonNumeric>
        <ix:nonNumeric name="dei:TradingSymbol" contextRef="class-a">EXAM</ix:nonNumeric>
        <ix:nonNumeric name="dei:SecurityExchangeName" contextRef="class-a">Nasdaq</ix:nonNumeric>
        """
        result = classify_security_classes(extract_security_classes(html))
        self.assertEqual(result["eligibility_status"], "eligible")
        self.assertEqual(result["eligible_security_classes"][0]["ticker"], "EXAM")

    def test_rejects_only_preferred_security(self) -> None:
        result = classify_security_classes(
            [{"security_title": "Preferred Stock", "ticker": "PREF", "exchange": "NYSE"}]
        )
        self.assertEqual(result["eligibility_status"], "ineligible")

    def test_keeps_incomplete_cover_evidence_pending(self) -> None:
        result = classify_security_classes([{"ticker": "EXAM"}])
        self.assertEqual(result["eligibility_status"], "pending")


if __name__ == "__main__":
    unittest.main()
