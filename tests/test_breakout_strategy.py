"""Tests for BreakoutStrategy: MaxPrev/MinPrev calculations, entry/exit signals."""
from __future__ import annotations

import pytest
import numpy as np
import pandas as pd

from backend.strategies.base import PositionState, Signal
from backend.strategies.breakout import BreakoutStrategy
from backend.download_engine import INTERVAL_MS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(closes, highs=None, lows=None, opens=None) -> pd.DataFrame:
    """Build a minimal DataFrame for strategy testing."""
    n = len(closes)
    if highs is None:
        highs = [c + 1.0 for c in closes]
    if lows is None:
        lows = [c - 1.0 for c in closes]
    if opens is None:
        opens = closes

    step = INTERVAL_MS["1d"]
    return pd.DataFrame({
        "open_time": [step * i for i in range(n)],
        "open": [float(v) for v in opens],
        "high": [float(v) for v in highs],
        "low": [float(v) for v in lows],
        "close": [float(v) for v in closes],
        "volume": [1000.0] * n,
    })


def _run_strategy(closes, highs=None, lows=None, opens=None, params=None):
    """Run strategy on given OHLCV data, return (strategy, df)."""
    df = _make_df(closes, highs, lows, opens)
    strat = BreakoutStrategy()
    default_params = {
        "N_entrada": 5,
        "M_salida": 3,
        "stop_pct": 0.05,
        "modo_ejecucion": "open_next",
        "habilitar_long": True,
        "habilitar_short": True,
        "coste_total_bps": 10.0,
    }
    if params:
        default_params.update(params)
    strat.init(default_params, df)
    return strat, df


# ---------------------------------------------------------------------------
# MaxPrev / MinPrev computation
# ---------------------------------------------------------------------------

class TestMaxPrevMinPrev:
    def test_max_prev_excludes_current(self):
        """max_prev at t should use [t-N, t-1], not t."""
        closes = list(range(1, 21))      # 1..20
        highs  = [float(c) + 0.5 for c in closes]
        strat, df = _run_strategy(closes, highs=highs, params={"N_entrada": 5})

        # At t=10: max of highs[5..9] = high[9] = 9.5 + 0.5 = 9.5 ... let's compute
        t = 10
        expected_max = max(highs[t - 5: t])
        assert abs(float(strat.max_prev.iloc[t]) - expected_max) < 1e-6

    def test_min_prev_excludes_current(self):
        lows = [float(100 - i) for i in range(30)]   # 100, 99, 98, ...
        strat, df = _run_strategy(
            closes=[float(100 - i) for i in range(30)],
            lows=lows,
            params={"N_entrada": 5},
        )
        t = 10
        expected_min = min(lows[t - 5: t])
        assert abs(float(strat.min_prev.iloc[t]) - expected_min) < 1e-6

    def test_warmup_period_is_nan(self):
        """First N-1 values should be NaN (not enough history)."""
        strat, df = _run_strategy(list(range(1, 21)), params={"N_entrada": 5})
        # shift(1).rolling(5) — first non-NaN is at index 5
        for t in range(5):
            assert pd.isna(strat.max_prev.iloc[t])

    def test_max_prev_after_warmup_not_nan(self):
        strat, df = _run_strategy(list(range(1, 21)), params={"N_entrada": 5})
        assert not pd.isna(strat.max_prev.iloc[5])


# ---------------------------------------------------------------------------
# Entry signals
# ---------------------------------------------------------------------------

class TestEntrySignals:
    def test_entry_long_on_breakout_above_max(self):
        """Close > MaxPrev should generate entry_long when flat."""
        # First 5 candles: close=10; candle 6: close=20 (breakout)
        closes = [10.0] * 10 + [20.0]
        strat, df = _run_strategy(closes, params={"N_entrada": 5})

        state = PositionState()
        signals = strat.on_candle(10, df.iloc[10], state)
        actions = [s.action for s in signals]
        assert "entry_long" in actions

    def test_entry_short_on_breakout_below_min(self):
        """Close < MinPrev should generate entry_short when flat."""
        closes = [100.0] * 10 + [50.0]
        strat, df = _run_strategy(closes, params={"N_entrada": 5})

        state = PositionState()
        signals = strat.on_candle(10, df.iloc[10], state)
        actions = [s.action for s in signals]
        assert "entry_short" in actions

    def test_no_entry_long_when_disabled(self):
        closes = [10.0] * 10 + [20.0]
        strat, df = _run_strategy(closes, params={"N_entrada": 5, "habilitar_long": False})

        state = PositionState()
        signals = strat.on_candle(10, df.iloc[10], state)
        assert not any(s.action == "entry_long" for s in signals)

    def test_no_entry_short_when_disabled(self):
        closes = [100.0] * 10 + [50.0]
        strat, df = _run_strategy(closes, params={"N_entrada": 5, "habilitar_short": False})

        state = PositionState()
        signals = strat.on_candle(10, df.iloc[10], state)
        assert not any(s.action == "entry_short" for s in signals)

    def test_no_entry_when_position_open(self):
        """Should not generate entry signal when already in a position."""
        closes = [10.0] * 10 + [20.0]
        strat, df = _run_strategy(closes, params={"N_entrada": 5})

        state = PositionState(side="long", entry_price=10.0, quantity=1.0, stop_price=8.0)
        signals = strat.on_candle(10, df.iloc[10], state)
        assert not any(s.action in ("entry_long", "entry_short") for s in signals)

    def test_no_entry_during_warmup(self):
        """During warmup period, no signals should be generated."""
        closes = list(range(1, 6))
        strat, df = _run_strategy(closes, params={"N_entrada": 10})

        state = PositionState()
        for t in range(5):
            signals = strat.on_candle(t, df.iloc[t], state)
            assert signals == []

    def test_stop_price_correct_for_long(self):
        """Stop for long entry = min_prev * (1 - stop_pct)."""
        lows = [90.0] * 10 + [90.0]
        closes = [100.0] * 10 + [200.0]  # breakout on t=10
        strat, df = _run_strategy(closes, lows=lows, params={"N_entrada": 5, "stop_pct": 0.10})

        state = PositionState()
        signals = strat.on_candle(10, df.iloc[10], state)
        entry = next((s for s in signals if s.action == "entry_long"), None)
        assert entry is not None
        min_prev_val = float(strat.min_prev.iloc[10])
        expected_stop = min_prev_val * (1.0 - 0.10)
        assert abs(entry.stop_price - expected_stop) < 1e-6

    def test_stop_price_correct_for_short(self):
        """Stop for short entry = max_prev * (1 + stop_pct)."""
        highs = [110.0] * 10 + [110.0]
        closes = [100.0] * 10 + [20.0]  # breakdown on t=10
        strat, df = _run_strategy(closes, highs=highs, params={"N_entrada": 5, "stop_pct": 0.10})

        state = PositionState()
        signals = strat.on_candle(10, df.iloc[10], state)
        entry = next((s for s in signals if s.action == "entry_short"), None)
        assert entry is not None
        max_prev_val = float(strat.max_prev.iloc[10])
        expected_stop = max_prev_val * (1.0 + 0.10)
        assert abs(entry.stop_price - expected_stop) < 1e-6


# ---------------------------------------------------------------------------
# Exit signals
# ---------------------------------------------------------------------------

class TestExitSignals:
    def test_exit_long_when_close_below_min_exit(self):
        """In long position, exit when Close < MinExit (stop not triggered)."""
        closes = [100.0] * 15
        strat, df = _run_strategy(closes, params={"N_entrada": 5, "M_salida": 3})

        # close slightly below min_exit but low stays above stop_price
        row = df.iloc[10].copy()
        row["close"] = 98.0   # below min_exit (rolling min of lows=99.0)
        row["low"] = 98.5     # still above stop_price=50.0 — stop won't fire

        state = PositionState(side="long", entry_price=90.0, stop_price=50.0, quantity=1.0)
        signals = strat.on_candle(10, row, state)
        assert any(s.action == "exit_long" for s in signals)

    def test_exit_short_when_close_above_max_exit(self):
        """In short position, exit when Close > MaxExit (stop not triggered)."""
        closes = [100.0] * 15
        strat, df = _run_strategy(closes, params={"N_entrada": 5, "M_salida": 3})

        # close slightly above max_exit but high stays below stop_price
        row = df.iloc[10].copy()
        row["close"] = 102.0   # above max_exit (rolling max of highs=101.0)
        row["high"] = 101.5    # still below stop_price=200.0 — stop won't fire

        state = PositionState(side="short", entry_price=110.0, stop_price=200.0, quantity=1.0)
        signals = strat.on_candle(10, row, state)
        assert any(s.action == "exit_short" for s in signals)

    def test_no_exit_if_close_within_range(self):
        """No exit signal if close is within normal range while in position."""
        closes = [100.0] * 15
        strat, df = _run_strategy(closes, params={"N_entrada": 5, "M_salida": 3})

        state = PositionState(side="long", entry_price=90.0, stop_price=80.0, quantity=1.0)
        # Close=100 with min_exit=99 -> no exit
        signals = strat.on_candle(10, df.iloc[10], state)
        assert not any(s.action in ("exit_long", "exit_short") for s in signals)


# ---------------------------------------------------------------------------
# Stop loss signals
# ---------------------------------------------------------------------------

class TestStopLossSignals:
    def test_stop_long_triggered_by_low(self):
        """Stop for long triggered when Low <= stop_price."""
        closes = [100.0] * 15
        strat, df = _run_strategy(closes, params={"N_entrada": 5, "M_salida": 3})

        row = df.iloc[10].copy()
        row["low"] = 79.0  # below stop

        state = PositionState(side="long", entry_price=100.0, stop_price=80.0, quantity=1.0)
        signals = strat.on_candle(10, row, state)
        assert any(s.action == "stop_long" for s in signals)

    def test_stop_short_triggered_by_high(self):
        """Stop for short triggered when High >= stop_price."""
        closes = [100.0] * 15
        strat, df = _run_strategy(closes, params={"N_entrada": 5, "M_salida": 3})

        row = df.iloc[10].copy()
        row["high"] = 121.0  # above stop

        state = PositionState(side="short", entry_price=100.0, stop_price=120.0, quantity=1.0)
        signals = strat.on_candle(10, row, state)
        assert any(s.action == "stop_short" for s in signals)

    def test_stop_takes_priority_over_exit(self):
        """When both stop and exit conditions are met, stop is returned (returns early)."""
        closes = [100.0] * 15
        lows = [99.0] * 15
        strat, df = _run_strategy(closes, lows=lows, params={"N_entrada": 5, "M_salida": 3})

        row = df.iloc[10].copy()
        row["low"] = 50.0   # below stop
        row["close"] = 0.01  # also below min_exit

        state = PositionState(side="long", entry_price=100.0, stop_price=80.0, quantity=1.0)
        signals = strat.on_candle(10, row, state)
        assert signals[0].action == "stop_long"
        assert len(signals) == 1  # only stop, not also exit


# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------

class TestParameterDefs:
    def test_get_parameters_returns_all(self):
        strat = BreakoutStrategy()
        params = strat.get_parameters()
        names = {p.name for p in params}
        expected = {"N_entrada", "M_salida", "stop_pct", "modo_ejecucion",
                    "habilitar_long", "habilitar_short", "coste_total_bps"}
        assert expected == names

    def test_strategy_name(self):
        assert BreakoutStrategy.name == "breakout"
