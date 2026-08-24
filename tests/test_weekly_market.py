from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from finance_news.weekly.market import (
    DailyBar,
    MarketIngestionError,
    calculate_market_features,
    load_daily_bars,
    store_daily_bars,
    store_market_features,
)
from finance_news.weekly.storage import connect_database, migrate_database


COLLECTED_AT = datetime(2026, 8, 23, tzinfo=timezone.utc)


def bars(symbol, closes, *, start=date(2026, 7, 1), volumes=None, provider="fixture"):
    volumes = volumes or [1_000_000] * len(closes)
    return tuple(
        DailyBar(
            symbol=symbol,
            session_date=start + timedelta(days=index),
            open=value,
            high=value,
            low=value,
            close=value,
            adjusted_close=value,
            volume=volumes[index],
            currency="USD",
            provider=provider,
            collected_at=COLLECTED_AT,
        )
        for index, value in enumerate(closes)
    )


class DailyBarTests(unittest.TestCase):
    def test_rejects_nonpositive_adjusted_close(self):
        with self.assertRaisesRegex(MarketIngestionError, "positive"):
            DailyBar("ABC", date(2026, 8, 1), 1, 1, 1, 1, 0, 100, "USD", "fixture", COLLECTED_AT)

    def test_rejects_naive_collection_time(self):
        with self.assertRaisesRegex(MarketIngestionError, "timezone-aware"):
            DailyBar("ABC", date(2026, 8, 1), 1, 1, 1, 1, 1, 100, "USD", "fixture", datetime(2026, 8, 1))


class MarketStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "weekly.sqlite3"
        migrate_database(self.database)
        self.connection = connect_database(self.database)

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def test_stores_and_loads_bars_in_date_order(self):
        source = tuple(reversed(bars("ABC", [100, 101, 102])))
        self.assertEqual(store_daily_bars(self.connection, source), 3)
        loaded = load_daily_bars(
            self.connection, "ABC", "fixture", date(2026, 7, 1), date(2026, 7, 3)
        )
        self.assertEqual([item.adjusted_close for item in loaded], [100, 101, 102])

    def test_repeated_ingestion_is_idempotent(self):
        source = bars("ABC", [100, 101, 102])
        store_daily_bars(self.connection, source)
        store_daily_bars(self.connection, source)
        count = self.connection.execute("SELECT COUNT(*) AS count FROM daily_market_bars").fetchone()["count"]
        self.assertEqual(count, 3)

    def test_rejects_conflicting_stored_bar(self):
        store_daily_bars(self.connection, bars("ABC", [100]))
        with self.assertRaisesRegex(MarketIngestionError, "conflicts"):
            store_daily_bars(self.connection, bars("ABC", [101]))

    def test_persists_reproducible_feature_inputs(self):
        self._seed_company()
        stock = bars("ABC", [100 + index for index in range(30)])
        benchmark = bars("SPY", [100 + index * 0.2 for index in range(30)])
        features = calculate_market_features(stock, benchmark)
        store_market_features(self.connection, "0000000001", features)
        rows = self.connection.execute(
            "SELECT * FROM market_features WHERE company_id = '0000000001'"
        ).fetchall()
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row["input_start"] == "2026-07-01" for row in rows))
        self.assertTrue(all(row["benchmark_symbol"] == "SPY" for row in rows))

    def _seed_company(self):
        self.connection.execute(
            "INSERT INTO companies "
            "(company_id, current_ticker, company_name, created_at, updated_at) "
            "VALUES ('0000000001', 'ABC', 'Fictional', '2026-08-23T00:00:00Z', '2026-08-23T00:00:00Z')"
        )
        self.connection.commit()


class MarketFeatureTests(unittest.TestCase):
    def test_calculates_benchmark_relative_five_day_return(self):
        stock = bars("ABC", [100] * 25 + [98, 96, 94, 92, 90])
        benchmark = bars("SPY", [100] * 30)
        result = calculate_market_features(stock, benchmark)
        self.assertAlmostEqual(result.value("five_day_return"), -0.10)
        self.assertAlmostEqual(result.value("abnormal_five_day_return"), -0.10)
        self.assertLess(result.value("abnormal_return_z_score"), 0)

    def test_average_dollar_volume_uses_recent_twenty_sessions(self):
        stock = bars("ABC", [10] * 30, volumes=[100] * 10 + [200] * 20)
        benchmark = bars("SPY", [100] * 30)
        result = calculate_market_features(stock, benchmark)
        self.assertEqual(result.value("average_dollar_volume_20d"), 2_000)

    def test_adjusted_prices_prevent_split_from_appearing_as_decline(self):
        stock = list(bars("ABC", [100] * 30))
        split_day = stock[-1]
        stock[-1] = DailyBar(
            symbol="ABC", session_date=split_day.session_date, open=50, high=50, low=50,
            close=50, adjusted_close=100, volume=2_000_000, currency="USD",
            provider="fixture", collected_at=COLLECTED_AT,
        )
        result = calculate_market_features(tuple(stock), bars("SPY", [100] * 30))
        self.assertEqual(result.value("five_day_return"), 0)

    def test_requires_sufficient_aligned_history(self):
        with self.assertRaisesRegex(MarketIngestionError, "21"):
            calculate_market_features(bars("ABC", [100] * 20), bars("SPY", [100] * 20))


if __name__ == "__main__":
    unittest.main()
