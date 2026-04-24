"""Tests for BreakoutTrailingStrategy: verifies move_stop emission rules."""

from __future__ import annotations

import pandas as pd

from backend.download_engine import INTERVAL_MS
from backend.strategies.base import PositionState
from backend.strategies.breakout_trailing import BreakoutTrailingStrategy


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
        "N_entrada": 5,
        "M_salida": 3,
        "stop_pct": 0.05,
        "modo_ejecucion": "open_next",
        "habilitar_long": True,
        "habilitar_short": True,
        "coste_total_bps": 10.0,
        "trailing_lookback": 3,
    }
    defaults.update(params)
    strat = BreakoutTrailingStrategy()
    strat.init(defaults, df)
    return strat


def test_long_move_stop_emitted_when_rolling_min_rises():
    """With a long open, a newer trailing-Min above the current stop should emit move_stop."""
    # Rising candles: trailing min keeps climbing.
    lows = [90, 91, 92, 93, 94, 95, 96]
    closes = [100, 101, 102, 103, 104, 105, 106]
    highs = [c + 1 for c in closes]
    df = _make_df(closes, highs=highs, lows=lows)
    strat = _init_strategy(df, trailing_lookback=3, stop_pct=0.02)

    # Position open, current stop below the trailing candidate.
    state = PositionState(side="long", entry_price=100.0, stop_price=80.0, quantity=10.0)
    signals = strat.on_candle(6, df.iloc[6], state)

    move_signals = [s for s in signals if s.action == "move_stop"]
    assert len(move_signals) == 1
    # Trailing min at t=6 over [t-3..t-1] = lows[3..5] = min(93,94,95) = 93
    # stop candidate = 93 * (1 - 0.02) = 91.14
    assert abs(move_signals[0].stop_price - 93.0 * (1 - 0.02)) < 1e-6


def test_long_no_move_stop_when_trailing_would_loosen():
    """Current stop is already tighter than the trailing candidate → no move_stop."""
    lows = [90, 91, 92, 93, 94, 95, 96]
    closes = [100, 101, 102, 103, 104, 105, 106]
    df = _make_df(closes, highs=[c + 1 for c in closes], lows=lows)
    strat = _init_strategy(df, trailing_lookback=3, stop_pct=0.02)

    # Stop already above trailing candidate (93*0.98 = 91.14).
    state = PositionState(side="long", entry_price=100.0, stop_price=95.0, quantity=10.0)
    signals = strat.on_candle(6, df.iloc[6], state)
    assert not any(s.action == "move_stop" for s in signals)


def test_short_move_stop_mirror_behaviour():
    """With a short open, a newer trailing-Max below current stop should emit move_stop."""
    highs = [110, 109, 108, 107, 106, 105, 104]
    closes = [100, 99, 98, 97, 96, 95, 94]
    lows = [c - 1 for c in closes]
    df = _make_df(closes, highs=highs, lows=lows)
    strat = _init_strategy(df, trailing_lookback=3, stop_pct=0.02)

    state = PositionState(side="short", entry_price=100.0, stop_price=120.0, quantity=10.0)
    signals = strat.on_candle(6, df.iloc[6], state)

    move_signals = [s for s in signals if s.action == "move_stop"]
    assert len(move_signals) == 1
    # Trailing max at t=6 over lows/highs[3..5] = max(107,106,105) = 107
    # stop candidate = 107 * (1 + 0.02) = 109.14
    assert abs(move_signals[0].stop_price - 107.0 * (1 + 0.02)) < 1e-6


def test_no_move_stop_when_flat():
    df = _make_df([100, 101, 102, 103, 104, 105, 106])
    strat = _init_strategy(df)
    state = PositionState()  # flat
    signals = strat.on_candle(6, df.iloc[6], state)
    assert not any(s.action == "move_stop" for s in signals)


def test_base_entry_still_works():
    """Inheritance must not break the base entry logic."""
    # Breakout up: long breakout on candle 6.
    closes = [100, 100, 100, 100, 100, 100, 150]
    highs = [101, 101, 101, 101, 101, 101, 151]
    lows = [99, 99, 99, 99, 99, 99, 149]
    df = _make_df(closes, highs=highs, lows=lows)
    strat = _init_strategy(df, N_entrada=5, stop_pct=0.02)

    state = PositionState()  # flat
    signals = strat.on_candle(6, df.iloc[6], state)
    entries = [s for s in signals if s.action == "entry_long"]
    assert len(entries) == 1
