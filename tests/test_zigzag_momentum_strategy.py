"""Tests for ZigzagMomentumStrategy: zigzag entries, RSI gating, ATR-padded trailing."""

from __future__ import annotations

import pandas as pd

from backend.download_engine import INTERVAL_MS
from backend.strategies.base import PositionState
from backend.strategies.zigzag_momentum import ZigzagMomentumStrategy


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


def _default_params(**overrides):
    p = {
        "reversal_pct": 0.02,
        "rsi_period": 5,
        "rsi_long_threshold": 0.0,  # disabled by default for tests
        "rsi_short_threshold": 100.0,  # disabled
        "atr_period": 5,
        "atr_buffer_mult": 1.0,
        "modo_ejecucion": "open_next",
        "habilitar_long": True,
        "habilitar_short": True,
        "salida_por_ruptura": True,
        "coste_total_bps": 10.0,
    }
    p.update(overrides)
    return p


def _run_strategy(closes, highs=None, lows=None, opens=None, params=None):
    df = _make_df(closes, highs, lows, opens)
    strat = ZigzagMomentumStrategy()
    strat.init(params or _default_params(), df)
    return strat, df


def test_get_parameters_includes_rsi_thresholds():
    strat = ZigzagMomentumStrategy()
    names = [p.name for p in strat.get_parameters()]
    assert "reversal_pct" in names
    assert "rsi_long_threshold" in names
    assert "rsi_short_threshold" in names
    assert "atr_buffer_mult" in names


def test_default_rsi_long_is_30():
    strat = ZigzagMomentumStrategy()
    p = {x.name: x.default for x in strat.get_parameters()}
    assert p["rsi_long_threshold"] == 30.0
    assert p["atr_buffer_mult"] == 1.5


def test_no_signals_before_zigzag_confirmed():
    # Short flat series — neither support nor resistance gets confirmed
    closes = [100.0] * 4
    strat, df = _run_strategy(closes)
    state = PositionState()
    for t in range(4):
        sigs = strat.on_candle(t, df.iloc[t], state)
        assert sigs == []


def test_long_entry_after_zigzag_levels_form():
    # Build pivots: peak at 110, trough at 95, then breakout above 110.
    closes = [100, 105, 110, 105, 100, 95, 96, 100, 105, 112]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    strat, df = _run_strategy(closes, highs=highs, lows=lows, params=_default_params(reversal_pct=0.02))
    state = PositionState()
    # Iterate to last candle to confirm zigzag has populated
    for t in range(len(df)):
        sigs = strat.on_candle(t, df.iloc[t], state)
        if any(s.action == "entry_long" for s in sigs):
            return
    raise AssertionError("expected an entry_long after zigzag confirmation + breakout")


def test_rsi_filter_blocks_long_when_threshold_too_high():
    # Same data, but require RSI > 99 — basically impossible with synthetic data
    closes = [100, 105, 110, 105, 100, 95, 96, 100, 105, 112]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    strat, df = _run_strategy(
        closes, highs=highs, lows=lows, params=_default_params(reversal_pct=0.02, rsi_long_threshold=99.0)
    )
    state = PositionState()
    for t in range(len(df)):
        sigs = strat.on_candle(t, df.iloc[t], state)
        assert all(s.action != "entry_long" for s in sigs)


def test_long_stop_hit_on_low_breach():
    # Stop check requires the zigzag to have produced both support and
    # resistance levels (mirrors support_resistance.py — early return when
    # either level is NaN). Use a series with confirmed pivots, then last
    # candle's low pierces stop.
    closes = [100, 105, 110, 105, 100, 95, 96, 100, 105, 90]
    highs = [c + 0.5 for c in closes]
    lows = [99.5, 104.5, 109.5, 104.5, 99.5, 94.5, 95.5, 99.5, 104.5, 89.0]
    strat, df = _run_strategy(closes, highs=highs, lows=lows, params=_default_params(reversal_pct=0.02))
    state = PositionState(side="long", entry_price=110.0, stop_price=90.0)
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    assert any(s.action == "stop_long" for s in sigs)


def test_short_stop_hit_on_high_breach():
    closes = [100, 95, 90, 95, 100, 105, 104, 100, 95, 110]
    highs = [100.5, 95.5, 90.5, 95.5, 100.5, 105.5, 104.5, 100.5, 95.5, 111.0]
    lows = [c - 0.5 for c in closes]
    strat, df = _run_strategy(closes, highs=highs, lows=lows, params=_default_params(reversal_pct=0.02))
    state = PositionState(side="short", entry_price=90.0, stop_price=110.0)
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    assert any(s.action == "stop_short" for s in sigs)


def test_trailing_move_stop_only_when_tightens():
    # Build a long-side scenario where support has formed; price climbs further.
    # Trailing candidate = support - atr_buffer * atr; should not loosen.
    closes = [100, 105, 110, 105, 100, 95, 96, 100, 105, 112, 115]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    strat, df = _run_strategy(
        closes, highs=highs, lows=lows, params=_default_params(reversal_pct=0.02, atr_buffer_mult=0.0)
    )
    # Pretend long opened earlier with a very low stop
    state = PositionState(side="long", entry_price=100.0, stop_price=80.0)
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    moves = [s for s in sigs if s.action == "move_stop"]
    for m in moves:
        assert m.stop_price > state.stop_price


def test_disabling_long_blocks_entry_even_with_breakout():
    closes = [100, 105, 110, 105, 100, 95, 96, 100, 105, 112]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    strat, df = _run_strategy(
        closes, highs=highs, lows=lows, params=_default_params(reversal_pct=0.02, habilitar_long=False)
    )
    state = PositionState()
    for t in range(len(df)):
        sigs = strat.on_candle(t, df.iloc[t], state)
        assert all(s.action != "entry_long" for s in sigs)


def test_exit_on_opposite_zigzag_breakout_when_long():
    # After confirming pivots, simulate a close breaking back below the latest support
    closes = [100, 105, 110, 105, 100, 95, 96, 100, 92]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    strat, df = _run_strategy(closes, highs=highs, lows=lows, params=_default_params(reversal_pct=0.02))
    state = PositionState(side="long", entry_price=100.0, stop_price=80.0)
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    # Either an exit_long or a stop_long is acceptable — both close the position.
    actions = [s.action for s in sigs]
    assert "exit_long" in actions or "stop_long" in actions
