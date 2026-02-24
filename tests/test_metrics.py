"""Tests for backtest_metrics and metrics_engine: Sharpe, max drawdown, win rate, etc."""
from __future__ import annotations

import math
import pytest
import numpy as np
import pandas as pd

from backend.backtest_metrics import compute_backtest_metrics, _candles_per_year
from backend.metrics_engine import compute_metrics
from backend.download_engine import INTERVAL_MS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DAY_MS = INTERVAL_MS["1d"]


def _make_df(closes, highs=None, lows=None) -> pd.DataFrame:
    n = len(closes)
    if highs is None:
        highs = [c + 1.0 for c in closes]
    if lows is None:
        lows = [c - 1.0 for c in closes]
    return pd.DataFrame({
        "open_time": [DAY_MS * i for i in range(n)],
        "open": [float(c) for c in closes],
        "high": [float(h) for h in highs],
        "low": [float(l) for l in lows],
        "close": [float(c) for c in closes],
        "volume": [1000.0] * n,
    })


def _trade(pnl: float, duration: int = 1) -> dict:
    return {
        "entry_time": 0,
        "exit_time": duration * DAY_MS,
        "side": "long",
        "entry_price": 100.0,
        "exit_price": 100.0 + pnl,
        "pnl": pnl,
        "fees": 0.1,
        "exit_reason": "exit_long",
        "duration_candles": duration,
    }


# ===========================================================================
# backtest_metrics
# ===========================================================================

class TestNetProfit:
    def test_positive_net_profit(self):
        eq = [10_000.0, 11_000.0, 12_000.0]
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        assert abs(m["net_profit"] - 2000.0) < 0.01
        assert abs(m["net_profit_pct"] - 20.0) < 0.01

    def test_negative_net_profit(self):
        eq = [10_000.0, 9_000.0, 8_000.0]
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        assert m["net_profit"] < 0

    def test_flat_equity(self):
        eq = [10_000.0, 10_000.0, 10_000.0]
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        assert abs(m["net_profit"]) < 1e-6
        assert abs(m["net_profit_pct"]) < 1e-6


class TestMaxDrawdown:
    def test_no_drawdown_in_uptrend(self):
        eq = [100.0, 110.0, 120.0, 130.0]
        m = compute_backtest_metrics(eq, [], 100.0, DAY_MS)
        assert abs(m["max_drawdown_pct"]) < 1e-6

    def test_drawdown_50_pct(self):
        eq = [100.0, 200.0, 100.0]  # drops from peak 200 to 100 = -50%
        m = compute_backtest_metrics(eq, [], 100.0, DAY_MS)
        assert abs(m["max_drawdown_pct"] - (-50.0)) < 0.01

    def test_drawdown_from_initial(self):
        eq = [100.0, 80.0, 60.0]   # drops from 100 to 60 = -40%
        m = compute_backtest_metrics(eq, [], 100.0, DAY_MS)
        assert abs(m["max_drawdown_pct"] - (-40.0)) < 0.01

    def test_multiple_drawdowns_picks_worst(self):
        # First dip: 100->80 (-20%), second dip: 120->60 (-50%)
        eq = [100.0, 80.0, 120.0, 60.0]
        m = compute_backtest_metrics(eq, [], 100.0, DAY_MS)
        assert m["max_drawdown_pct"] < -49.0


class TestWinRate:
    def test_all_wins(self):
        trades = [_trade(100.0), _trade(50.0), _trade(200.0)]
        eq = [10_000.0] * 10
        m = compute_backtest_metrics(eq, trades, 10_000.0, DAY_MS)
        assert abs(m["win_rate_pct"] - 100.0) < 1e-6

    def test_all_losses(self):
        trades = [_trade(-100.0), _trade(-50.0)]
        eq = [10_000.0] * 10
        m = compute_backtest_metrics(eq, trades, 10_000.0, DAY_MS)
        assert abs(m["win_rate_pct"]) < 1e-6

    def test_50_pct_win_rate(self):
        trades = [_trade(100.0), _trade(-100.0), _trade(100.0), _trade(-100.0)]
        eq = [10_000.0] * 10
        m = compute_backtest_metrics(eq, trades, 10_000.0, DAY_MS)
        assert abs(m["win_rate_pct"] - 50.0) < 1e-6

    def test_no_trades(self):
        eq = [10_000.0] * 10
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        assert m["n_trades"] == 0
        assert m["win_rate_pct"] == 0.0


class TestProfitFactor:
    def test_profit_factor_basic(self):
        trades = [_trade(200.0), _trade(-100.0)]
        eq = [10_000.0] * 10
        m = compute_backtest_metrics(eq, trades, 10_000.0, DAY_MS)
        assert abs(m["profit_factor"] - 2.0) < 0.01

    def test_profit_factor_all_wins_is_none_or_inf(self):
        trades = [_trade(100.0), _trade(200.0)]
        eq = [10_000.0] * 10
        m = compute_backtest_metrics(eq, trades, 10_000.0, DAY_MS)
        assert m["profit_factor"] is None or m["profit_factor"] > 1000.0


class TestExpectancy:
    def test_positive_expectancy(self):
        trades = [_trade(100.0), _trade(100.0), _trade(-50.0)]
        eq = [10_000.0] * 10
        m = compute_backtest_metrics(eq, trades, 10_000.0, DAY_MS)
        expected = (100.0 + 100.0 - 50.0) / 3
        assert abs(m["expectancy"] - expected) < 0.01


class TestSharpeRatio:
    def test_sharpe_positive_for_consistent_gains(self):
        # Consistent upward equity -> positive Sharpe
        eq = [10_000.0 * (1.001 ** i) for i in range(252)]
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        assert m["sharpe"] > 0

    def test_sharpe_negative_for_consistent_losses(self):
        eq = [10_000.0 * (0.999 ** i) for i in range(252)]
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        assert m["sharpe"] < 0

    def test_sharpe_zero_for_flat_equity(self):
        eq = [10_000.0] * 252
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        assert abs(m["sharpe"]) < 1e-9


class TestSortinoRatio:
    def test_sortino_positive_for_gains(self):
        # Use noisy but upward-trending equity so there are some downside candles
        import random
        random.seed(42)
        eq = [10_000.0]
        for _ in range(251):
            change = random.gauss(0.002, 0.01)  # positive drift, occasional losses
            eq.append(eq[-1] * (1 + change))
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        assert m["sortino"] > 0


class TestCagr:
    def test_cagr_zero_for_flat(self):
        eq = [10_000.0] * 365
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        assert abs(m["cagr_pct"]) < 0.01

    def test_cagr_double_in_one_year(self):
        # 365 daily candles, equity doubles
        n = 365
        eq = [10_000.0 + (10_000.0 / n) * i for i in range(n)]
        eq[-1] = 20_000.0
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        # CAGR should be approximately 100%
        assert m["cagr_pct"] > 90.0


class TestTimeInMarket:
    def test_time_in_market_with_trades(self):
        trades = [_trade(100.0, duration=10), _trade(50.0, duration=20)]
        eq = [10_000.0] * 100
        m = compute_backtest_metrics(eq, trades, 10_000.0, DAY_MS)
        expected_pct = 30.0 / 100.0 * 100
        assert abs(m["time_in_market_pct"] - expected_pct) < 0.01

    def test_time_in_market_zero_with_no_trades(self):
        eq = [10_000.0] * 50
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        assert m["time_in_market_pct"] == 0.0


class TestDrawdownCurve:
    def test_drawdown_curve_length_matches_equity(self):
        eq = [10_000.0, 11_000.0, 9_000.0, 10_000.0]
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        assert len(m["drawdown_curve"]) == len(eq)

    def test_drawdown_curve_non_positive(self):
        eq = [10_000.0, 11_000.0, 9_000.0, 10_500.0]
        m = compute_backtest_metrics(eq, [], 10_000.0, DAY_MS)
        for v in m["drawdown_curve"]:
            assert v <= 0.0 + 1e-9


class TestEdgeCases:
    def test_empty_equity_returns_empty(self):
        m = compute_backtest_metrics([], [], 10_000.0, DAY_MS)
        assert m == {}

    def test_zero_initial_capital_returns_empty(self):
        m = compute_backtest_metrics([100.0], [], 0.0, DAY_MS)
        assert m == {}


class TestCandlesPerYear:
    def test_daily(self):
        assert abs(_candles_per_year(DAY_MS) - 365.25) < 0.01

    def test_hourly(self):
        hour_ms = INTERVAL_MS["1h"]
        assert abs(_candles_per_year(hour_ms) - 365.25 * 24) < 0.1


# ===========================================================================
# metrics_engine.compute_metrics
# ===========================================================================

class TestComputeMetricsSMA:
    def test_sma_20_correct(self):
        closes = [float(i + 1) for i in range(100)]
        df = _make_df(closes)
        result = compute_metrics(df, selected=["sma_20"])
        assert "sma_20" in result
        # At t=19, SMA(20) = mean(1..20) = 10.5
        assert abs(result["sma_20"].iloc[19] - 10.5) < 1e-6

    def test_sma_nan_during_warmup(self):
        df = _make_df([float(i) for i in range(50)])
        result = compute_metrics(df, selected=["sma_20"])
        for i in range(19):  # first 19 values should be NaN
            assert pd.isna(result["sma_20"].iloc[i])

    def test_sma_not_nan_after_warmup(self):
        df = _make_df([float(i) for i in range(50)])
        result = compute_metrics(df, selected=["sma_20"])
        assert not pd.isna(result["sma_20"].iloc[19])


class TestComputeMetricsEMA:
    def test_ema_20_present(self):
        df = _make_df([float(i + 1) for i in range(50)])
        result = compute_metrics(df, selected=["ema_20"])
        assert "ema_20" in result
        assert len(result["ema_20"]) == 50

    def test_ema_no_nan_except_first(self):
        df = _make_df([float(i + 1) for i in range(50)])
        result = compute_metrics(df, selected=["ema_20"])
        # EMA with adjust=False: first value uses close[0] as seed, no NaN after that
        assert not any(pd.isna(result["ema_20"].iloc[1:]))


class TestComputeMetricsATR:
    def test_atr_14_present(self):
        df = _make_df([float(i + 100) for i in range(50)])
        result = compute_metrics(df, selected=["atr_14"])
        assert "atr_14" in result

    def test_atr_positive(self):
        df = _make_df([float(i + 100) for i in range(50)])
        result = compute_metrics(df, selected=["atr_14"])
        valid = result["atr_14"].dropna()
        assert (valid > 0).all()


class TestComputeMetricsDonchian:
    def test_donchian_upper_20(self):
        highs = [float(i + 1) for i in range(50)]
        df = _make_df([float(i) for i in range(50)], highs=highs)
        result = compute_metrics(df, selected=["donchian_upper_20"])
        assert "donchian_upper_20" in result
        # At t=19: max of highs[0..19] = 20.0
        assert abs(result["donchian_upper_20"].iloc[19] - 20.0) < 1e-6

    def test_donchian_lower_20(self):
        lows = [float(50 - i) for i in range(50)]
        df = _make_df([float(i + 50) for i in range(50)], lows=lows)
        result = compute_metrics(df, selected=["donchian_lower_20"])
        assert "donchian_lower_20" in result
        t = 19
        expected = min(lows[:20])
        assert abs(result["donchian_lower_20"].iloc[t] - expected) < 1e-6


class TestComputeMetricsReturns:
    def test_returns_simple_correct(self):
        closes = [100.0, 110.0, 99.0]
        df = _make_df(closes)
        result = compute_metrics(df, selected=["returns_simple"])
        assert pd.isna(result["returns_simple"].iloc[0])
        assert abs(result["returns_simple"].iloc[1] - 0.10) < 1e-6
        assert abs(result["returns_simple"].iloc[2] - (-11.0 / 110.0)) < 1e-6

    def test_returns_log_correct(self):
        closes = [100.0, math.e * 100]  # log return should be 1.0
        df = _make_df(closes)
        result = compute_metrics(df, selected=["returns_log"])
        assert abs(result["returns_log"].iloc[1] - 1.0) < 1e-6


class TestComputeMetricsAll:
    def test_all_metrics_computed_when_no_selection(self):
        df = _make_df([float(i + 100) for i in range(250)])
        result = compute_metrics(df)
        expected_keys = {
            "returns_log", "returns_simple", "range", "true_range",
            "sma_20", "sma_50", "sma_200",
            "ema_20", "ema_50", "ema_200",
            "volatility_20", "volatility_50",
            "atr_14", "atr_20",
            "rolling_max_20", "rolling_max_50",
            "rolling_min_20", "rolling_min_50",
            "donchian_upper_20", "donchian_upper_50",
            "donchian_lower_20", "donchian_lower_50",
        }
        assert expected_keys.issubset(set(result.keys()))

    def test_empty_df_returns_empty(self):
        result = compute_metrics(pd.DataFrame())
        assert result == {}

    def test_selection_limits_output(self):
        df = _make_df([float(i + 100) for i in range(50)])
        result = compute_metrics(df, selected=["sma_20", "ema_20"])
        assert set(result.keys()) == {"sma_20", "ema_20"}
