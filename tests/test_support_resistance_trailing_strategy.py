"""Tests for SupportResistanceTrailingStrategy: verifies move_stop emission rules."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.download_engine import INTERVAL_MS
from backend.strategies.base import PositionState
from backend.strategies.support_resistance_trailing import SupportResistanceTrailingStrategy


def _make_df(closes, highs=None, lows=None, opens=None) -> pd.DataFrame:
    n = len(closes)
    if highs is None:
        highs = [c + 1.0 for c in closes]
    if lows is None:
        lows = [c - 1.0 for c in closes]
    if opens is None:
        opens = closes

    step = INTERVAL_MS["1d"]
    return pd.DataFrame(
        {
            "open_time": [step * i for i in range(n)],
            "open": [float(v) for v in opens],
            "high": [float(v) for v in highs],
            "low": [float(v) for v in lows],
            "close": [float(v) for v in closes],
            "volume": [1000.0] * n,
        }
    )


def _init_strategy(df, **params):
    defaults = {
        "reversal_pct": 0.05,
        "stop_pct": 0.02,
        "modo_ejecucion": "open_next",
        "habilitar_long": True,
        "habilitar_short": True,
        "coste_total_bps": 10.0,
    }
    defaults.update(params)
    strat = SupportResistanceTrailingStrategy()
    strat.init(defaults, df)
    return strat


def test_long_move_stop_when_support_tightens():
    """When last_support at t yields a higher candidate than state.stop_price, emit move_stop."""
    df = _make_df([100] * 10)
    strat = _init_strategy(df, stop_pct=0.02)
    # Force a confirmed support at the current candle.
    strat.last_support = np.full(10, 95.0)
    strat.last_resistance = np.full(10, 110.0)

    state = PositionState(side="long", entry_price=100.0, stop_price=80.0, quantity=10.0)
    signals = strat.on_candle(5, df.iloc[5], state)
    moves = [s for s in signals if s.action == "move_stop"]
    assert len(moves) == 1
    assert abs(moves[0].stop_price - 95.0 * (1 - 0.02)) < 1e-6


def test_long_no_move_stop_when_support_unchanged():
    """If candidate equals current stop (no tightening), no move_stop."""
    df = _make_df([100] * 10)
    strat = _init_strategy(df, stop_pct=0.02)
    strat.last_support = np.full(10, 95.0)
    strat.last_resistance = np.full(10, 110.0)

    current_stop = 95.0 * (1 - 0.02)
    state = PositionState(side="long", entry_price=100.0, stop_price=current_stop, quantity=10.0)
    signals = strat.on_candle(5, df.iloc[5], state)
    assert not any(s.action == "move_stop" for s in signals)


def test_short_move_stop_when_resistance_tightens():
    df = _make_df([100] * 10)
    strat = _init_strategy(df, stop_pct=0.02)
    strat.last_support = np.full(10, 90.0)
    strat.last_resistance = np.full(10, 105.0)

    state = PositionState(side="short", entry_price=100.0, stop_price=120.0, quantity=10.0)
    signals = strat.on_candle(5, df.iloc[5], state)
    moves = [s for s in signals if s.action == "move_stop"]
    assert len(moves) == 1
    assert abs(moves[0].stop_price - 105.0 * (1 + 0.02)) < 1e-6


def test_no_move_stop_before_levels_confirmed():
    df = _make_df([100] * 10)
    strat = _init_strategy(df)
    # Levels still NaN (no confirmed support/resistance yet).
    strat.last_support = np.full(10, np.nan)
    strat.last_resistance = np.full(10, np.nan)

    state = PositionState(side="long", entry_price=100.0, stop_price=80.0, quantity=10.0)
    signals = strat.on_candle(5, df.iloc[5], state)
    assert not any(s.action == "move_stop" for s in signals)


def test_base_stop_signal_preempts_move_stop():
    """If the base strategy emits stop_long in the same candle, no trailing move is appended."""
    df = _make_df([100] * 10, lows=[c - 0.5 for c in [100] * 10])
    strat = _init_strategy(df, stop_pct=0.02)
    strat.last_support = np.full(10, 95.0)
    strat.last_resistance = np.full(10, 110.0)

    # state.stop_price is above today's low, so base will fire stop_long.
    state = PositionState(side="long", entry_price=100.0, stop_price=150.0, quantity=10.0)
    signals = strat.on_candle(5, df.iloc[5], state)
    assert any(s.action == "stop_long" for s in signals)
    assert not any(s.action == "move_stop" for s in signals)
