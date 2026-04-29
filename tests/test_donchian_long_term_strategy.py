"""Tests for DonchianLongTermStrategy: rolling-min/max trailing, optional SMA filter."""

from __future__ import annotations

import pandas as pd

from backend.download_engine import INTERVAL_MS
from backend.strategies.base import PositionState
from backend.strategies.donchian_long_term import DonchianLongTermStrategy


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
        "sma_filter_n": 0,
        "trail_lookback": 3,
        "atr_period": 5,
        "atr_buffer_mult": 0.0,
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
    strat = DonchianLongTermStrategy()
    strat.init(params or _default_params(), df)
    return strat, df


def test_get_parameters_includes_sma_filter():
    strat = DonchianLongTermStrategy()
    names = [p.name for p in strat.get_parameters()]
    assert "sma_filter_n" in names
    assert "trail_lookback" in names
    assert "atr_buffer_mult" in names


def test_default_donchian_n_is_5():
    strat = DonchianLongTermStrategy()
    p = {x.name: x.default for x in strat.get_parameters()}
    assert p["donchian_n"] == 5
    assert p["sma_filter_n"] == 0


def test_no_signals_before_warmup():
    closes = [100.0] * 4
    strat, df = _run_strategy(closes)
    state = PositionState()
    for t in range(4):
        sigs = strat.on_candle(t, df.iloc[t], state)
        assert sigs == []


def test_long_entry_on_donchian_breakout_no_filter():
    closes = [100.0] * 6 + [115.0]
    highs = [101.0] * 6 + [116.0]
    lows = [99.0] * 6 + [114.0]
    strat, df = _run_strategy(closes, highs=highs, lows=lows)
    state = PositionState()
    sigs = strat.on_candle(6, df.iloc[6], state)
    assert any(s.action == "entry_long" for s in sigs)


def test_sma_filter_blocks_long_when_below_sma():
    # Series in clear downtrend, then a tiny up-bar that breaks the (very short)
    # 5-prev high. SMA(5) should be above the current close → long blocked.
    closes = [120.0, 115.0, 110.0, 105.0, 100.0, 95.0, 96.0]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    strat, df = _run_strategy(
        closes,
        highs=highs,
        lows=lows,
        params=_default_params(donchian_n=5, sma_filter_n=5),
    )
    state = PositionState()
    sigs = strat.on_candle(6, df.iloc[6], state)
    assert all(s.action != "entry_long" for s in sigs)


def test_short_entry_on_breakdown():
    closes = [100.0] * 6 + [85.0]
    lows = [99.0] * 6 + [84.0]
    strat, df = _run_strategy(closes, lows=lows)
    state = PositionState()
    sigs = strat.on_candle(6, df.iloc[6], state)
    assert any(s.action == "entry_short" for s in sigs)


def test_long_stop_hit_on_low_breach():
    closes = [110.0] * 7
    lows = [109.0] * 6 + [89.0]
    strat, df = _run_strategy(closes, lows=lows)
    state = PositionState(side="long", entry_price=110.0, stop_price=90.0)
    sigs = strat.on_candle(6, df.iloc[6], state)
    assert any(s.action == "stop_long" for s in sigs)


def test_long_exit_on_opposite_donchian_break():
    closes = [110.0, 112.0, 111.0, 100.0]
    strat, df = _run_strategy(
        [110.0] * 6 + closes,
        params=_default_params(donchian_exit_n=3),
    )
    state = PositionState(side="long", entry_price=110.0, stop_price=90.0)
    sigs = strat.on_candle(9, df.iloc[9], state)
    assert any(s.action == "exit_long" for s in sigs)


def test_trailing_move_stop_only_on_tighten():
    # Price marches up; trailing should tighten the stop.
    closes = list(range(100, 110))
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    strat, df = _run_strategy(closes, highs=highs, lows=lows, params=_default_params(trail_lookback=3))
    state = PositionState(side="long", entry_price=100.0, stop_price=95.0)
    sigs = strat.on_candle(9, df.iloc[9], state)
    for s in sigs:
        if s.action == "move_stop":
            assert s.stop_price > state.stop_price


def test_no_loosening_move_stop():
    closes = [105.0] * 6 + [102.0]
    strat, df = _run_strategy(closes, params=_default_params(trail_lookback=3))
    # Stop already tighter than the rolling 3-low candidate
    state = PositionState(side="long", entry_price=104.0, stop_price=104.0)
    sigs = strat.on_candle(6, df.iloc[6], state)
    moves = [s for s in sigs if s.action == "move_stop"]
    assert moves == []


def test_atr_buffer_mult_zero_uses_extreme_directly():
    # With atr_buffer_mult=0 the trailing stop = trail_min[t] exactly.
    closes = [100.0] * 6 + [108.0]
    highs = [101.0] * 6 + [109.0]
    lows = [99.0, 99.0, 99.0, 99.5, 100.0, 101.0, 107.0]
    strat, df = _run_strategy(
        closes, highs=highs, lows=lows, params=_default_params(trail_lookback=3, atr_buffer_mult=0.0)
    )
    state = PositionState(side="long", entry_price=100.0, stop_price=98.0)
    sigs = strat.on_candle(6, df.iloc[6], state)
    moves = [s for s in sigs if s.action == "move_stop"]
    if moves:
        # rolling min of last 3 lows excluding t: min(99.5, 100.0, 101.0) = 99.5
        assert abs(moves[0].stop_price - 99.5) < 1e-6


def test_disabling_long_blocks_entry():
    closes = [100.0] * 6 + [115.0]
    highs = [101.0] * 6 + [116.0]
    strat, df = _run_strategy(closes, highs=highs, params=_default_params(habilitar_long=False))
    state = PositionState()
    sigs = strat.on_candle(6, df.iloc[6], state)
    assert all(s.action != "entry_long" for s in sigs)
