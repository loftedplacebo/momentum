import unittest

import pandas as pd

from crypto_momentum.backtest import MomentumBacktester, Position
from crypto_momentum.config import BacktestConfig


class TimeStopTests(unittest.TestCase):
    def test_time_stop_is_calendar_hours(self):
        entry = pd.Timestamp("2026-01-01T00:00:00Z")
        before_limit = entry + pd.Timedelta(minutes=15 * 191)
        at_limit = entry + pd.Timedelta(hours=48)
        prices = {"TESTUSDT": pd.DataFrame(
            {"open": [100.0, 100.0, 100.0], "high": [100.0, 100.0, 100.0],
             "low": [100.0, 100.0, 100.0], "close": [100.0, 100.0, 100.0]},
            index=[entry, before_limit, at_limit],
        )}
        position = Position("TESTUSDT", 1, 1.0, 100.0, entry, 100.0, 100.0, 0.0, 0.50)
        rank = pd.DataFrame({"symbol": ["TESTUSDT"], "long_rank": [1], "short_rank": [1]})
        engine = MomentumBacktester(BacktestConfig(time_stop_hours=48, trailing_profit_pct=0.50))
        self.assertEqual(engine._exits({"TESTUSDT": position}, prices, rank, before_limit), [])
        exits = engine._exits({"TESTUSDT": position}, prices, rank, at_limit)
        self.assertEqual(exits[0][1], "time_stop")


if __name__ == "__main__":
    unittest.main()
