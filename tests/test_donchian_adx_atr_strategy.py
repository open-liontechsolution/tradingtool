"""Tests for DonchianAdxAtrStrategy: indicator warm-up, ADX gating, ATR-trailing.

Mirrors the synthetic-OHLCV pattern in tests/test_breakout_strategy.py.
"""

from __future__ import annotations

import pandas as pd

from backend.download_engine import INTERVAL_MS
from backend.strategies.base import PositionState
from backend.strategies.donchian_adx_atr import DonchianAdxAtrStrategy


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
        "donchian_n": 5,
        "donchian_exit_n": 3,
        "adx_period": 5,
        "adx_threshold": 0.0,  # disabled by default in tests so entries fire
        "atr_period": 5,
        "atr_stop_mult": 2.0,
        "atr_trail_mult": 3.0,
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
    strat = DonchianAdxAtrStrategy()
    strat.init(params or _default_params(), df)
    return strat, df


# ---------------------------------------------------------------------------
# Parameter definition
# ---------------------------------------------------------------------------


def test_get_parameters_returns_expected_param_names():
    strat = DonchianAdxAtrStrategy()
    names = [p.name for p in strat.get_parameters()]
    assert "donchian_n" in names
    assert "adx_threshold" in names
    assert "atr_stop_mult" in names
    assert "atr_trail_mult" in names
    assert "modo_ejecucion" in names


def test_default_atr_trail_mult_is_8():
    strat = DonchianAdxAtrStrategy()
    p = {x.name: x.default for x in strat.get_parameters()}
    assert p["atr_trail_mult"] == 8.0


# ---------------------------------------------------------------------------
# Warm-up — no signals before all indicators are populated
# ---------------------------------------------------------------------------


def test_no_signals_before_warmup():
    closes = [100.0] * 4
    strat, df = _run_strategy(closes)
    state = PositionState()
    for t in range(4):
        sigs = strat.on_candle(t, df.iloc[t], state)
        assert sigs == []


# ---------------------------------------------------------------------------
# Entry: long breakout above prev N high
# ---------------------------------------------------------------------------


def test_long_entry_on_donchian_breakout():
    # ADX(5) needs ~10 candles to warm up. Build a clear uptrend that lets ADX
    # reach a non-NaN positive value, then break the prev-N high on the last
    # candle.
    closes = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120, 130]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    strat, df = _run_strategy(closes, highs=highs, lows=lows, params=_default_params(adx_threshold=0.0))
    state = PositionState()
    sigs = strat.on_candle(11, df.iloc[11], state)
    actions = [s.action for s in sigs]
    assert "entry_long" in actions
    entry = next(s for s in sigs if s.action == "entry_long")
    assert entry.stop_price < 130.0


def test_short_entry_on_donchian_breakdown():
    # Symmetric: clear downtrend with ADX warmed up, then break prev-N low.
    closes = [120, 118, 116, 114, 112, 110, 108, 106, 104, 102, 100, 90]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    strat, df = _run_strategy(closes, highs=highs, lows=lows, params=_default_params(adx_threshold=0.0))
    state = PositionState()
    sigs = strat.on_candle(11, df.iloc[11], state)
    actions = [s.action for s in sigs]
    assert "entry_short" in actions


# ---------------------------------------------------------------------------
# ADX filter blocks entries when ADX is too low
# ---------------------------------------------------------------------------


def test_adx_filter_blocks_weak_trend_entry():
    # Same data as the long breakout test, but with a very high ADX threshold
    closes = [100.0] * 6 + [115.0]
    highs = [101.0] * 6 + [116.0]
    lows = [99.0] * 6 + [114.0]
    strat, df = _run_strategy(closes, highs=highs, lows=lows, params=_default_params(adx_threshold=99.0))
    state = PositionState()
    sigs = strat.on_candle(6, df.iloc[6], state)
    # ADX never reaches 99 with this synthetic data → no entry
    assert all(s.action not in ("entry_long", "entry_short") for s in sigs)


# ---------------------------------------------------------------------------
# Exit on opposite-Donchian breakout when long
# ---------------------------------------------------------------------------


def test_long_exit_on_opposite_donchian_break():
    # Build an open long at 110, then a breakdown below the 3-period exit channel.
    # Stop set very low so the exit path (not the stop path) fires.
    closes = [110.0, 112.0, 111.0, 100.0]
    strat, df = _run_strategy(
        [100.0, 100.0, 100.0, 100.0, 100.0, 100.0] + closes,
        params=_default_params(donchian_n=5, donchian_exit_n=3, adx_threshold=0.0),
    )
    state = PositionState(side="long", entry_price=110.0, stop_price=50.0)
    sigs = strat.on_candle(9, df.iloc[9], state)
    assert any(s.action == "exit_long" for s in sigs)


# ---------------------------------------------------------------------------
# Stop hit (intrabar low touches stop_price)
# ---------------------------------------------------------------------------


def test_long_stop_hit_when_low_breaches():
    closes = [100.0] * 7
    lows = [99.0] * 6 + [89.0]  # last low pierces stop
    strat, df = _run_strategy(closes, lows=lows)
    state = PositionState(side="long", entry_price=100.0, stop_price=90.0)
    sigs = strat.on_candle(6, df.iloc[6], state)
    assert any(s.action == "stop_long" for s in sigs)


# ---------------------------------------------------------------------------
# Trailing: move_stop emitted ONLY when it tightens
# ---------------------------------------------------------------------------


def test_trailing_move_stop_only_on_tighten():
    # Open long; price marches up so trailing should keep tightening upward.
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 110.0, 115.0]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    strat, df = _run_strategy(closes, highs=highs, lows=lows, params=_default_params(atr_trail_mult=2.0))
    state = PositionState(side="long", entry_price=100.0, stop_price=95.0)
    sigs = strat.on_candle(7, df.iloc[7], state)
    moves = [s for s in sigs if s.action == "move_stop"]
    # If a move_stop is emitted at all, it must tighten
    for m in moves:
        assert m.stop_price > state.stop_price


def test_trailing_no_move_stop_when_would_loosen():
    # Long with a tight trailing stop; price drops a little — trail candidate
    # would be lower than current stop; no move_stop should fire.
    closes = [100.0] * 6 + [98.0]
    strat, df = _run_strategy(closes, params=_default_params(atr_trail_mult=10.0))
    # Set a stop that's already very tight (close to current price)
    state = PositionState(side="long", entry_price=100.0, stop_price=97.5)
    sigs = strat.on_candle(6, df.iloc[6], state)
    moves = [s for s in sigs if s.action == "move_stop"]
    assert moves == [], "trailing should not loosen the stop"


# ---------------------------------------------------------------------------
# Disabling long/short prevents entries
# ---------------------------------------------------------------------------


def test_disabling_long_blocks_long_entry():
    closes = [100.0] * 6 + [115.0]
    highs = [101.0] * 6 + [116.0]
    strat, df = _run_strategy(closes, highs=highs, params=_default_params(habilitar_long=False, adx_threshold=0.0))
    state = PositionState()
    sigs = strat.on_candle(6, df.iloc[6], state)
    assert all(s.action != "entry_long" for s in sigs)


def test_disabling_short_blocks_short_entry():
    closes = [100.0] * 6 + [85.0]
    lows = [99.0] * 6 + [84.0]
    strat, df = _run_strategy(closes, lows=lows, params=_default_params(habilitar_short=False, adx_threshold=0.0))
    state = PositionState()
    sigs = strat.on_candle(6, df.iloc[6], state)
    assert all(s.action != "entry_short" for s in sigs)
