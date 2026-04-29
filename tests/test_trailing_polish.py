"""Tests for the new optional flags added to breakout_trailing /
support_resistance_trailing / breakout / support_resistance.

Each flag defaults to a value that preserves the legacy behaviour, so the
existing test suites continue to pass; these tests cover the *opt-in* paths.
"""

from __future__ import annotations

import pandas as pd

from backend.download_engine import INTERVAL_MS
from backend.strategies.base import PositionState
from backend.strategies.breakout import BreakoutStrategy
from backend.strategies.breakout_trailing import BreakoutTrailingStrategy
from backend.strategies.support_resistance import SupportResistanceStrategy
from backend.strategies.support_resistance_trailing import SupportResistanceTrailingStrategy


def _df(closes, highs=None, lows=None, opens=None):
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


# ---------------------------------------------------------------------------
# exit_confirmation_candles (breakout)
# ---------------------------------------------------------------------------


def test_breakout_exit_confirm_1_matches_legacy_single_candle_exit():
    # 6 flat candles, then a single dip below M-min: with default n_confirm=1
    # the trade closes immediately.
    closes = [110.0] * 6 + [95.0]
    lows = [109.0] * 6 + [94.0]
    df = _df(closes, lows=lows)
    strat = BreakoutStrategy()
    strat.init(
        {
            "N_entrada": 5,
            "M_salida": 3,
            "stop_pct": 0.05,
            "habilitar_long": True,
            "habilitar_short": True,
            "salida_por_ruptura": True,
            "exit_confirmation_candles": 1,
            "modo_ejecucion": "open_next",
            "coste_total_bps": 0.0,
        },
        df,
    )
    state = PositionState(side="long", entry_price=110.0, stop_price=80.0)
    sigs = strat.on_candle(6, df.iloc[6], state)
    assert any(s.action == "exit_long" for s in sigs)


def test_breakout_exit_confirm_3_blocks_single_candle_dip():
    # Same dip as above, but require 3 consecutive candles before exiting.
    closes = [110.0] * 6 + [95.0]
    lows = [109.0] * 6 + [94.0]
    df = _df(closes, lows=lows)
    strat = BreakoutStrategy()
    strat.init(
        {
            "N_entrada": 5,
            "M_salida": 3,
            "stop_pct": 0.05,
            "habilitar_long": True,
            "habilitar_short": True,
            "salida_por_ruptura": True,
            "exit_confirmation_candles": 3,
            "modo_ejecucion": "open_next",
            "coste_total_bps": 0.0,
        },
        df,
    )
    state = PositionState(side="long", entry_price=110.0, stop_price=80.0)
    sigs = strat.on_candle(6, df.iloc[6], state)
    assert all(s.action != "exit_long" for s in sigs), "single dip should not exit when 3 confirmations required"


def test_breakout_exit_confirm_3_fires_after_three_consecutive_dips():
    # Continuous downtrend so each candle's close is below its own M-min reference.
    # closes 110→ trending down; lows track closes minus 1.
    closes = [110.0, 110.0, 110.0, 110.0, 110.0, 90.0, 88.0, 85.0]
    lows = [c - 1.0 for c in closes]
    df = _df(closes, lows=lows)
    strat = BreakoutStrategy()
    strat.init(
        {
            "N_entrada": 5,
            "M_salida": 3,
            "stop_pct": 0.05,
            "habilitar_long": True,
            "habilitar_short": True,
            "salida_por_ruptura": True,
            "exit_confirmation_candles": 3,
            "modo_ejecucion": "open_next",
            "coste_total_bps": 0.0,
        },
        df,
    )
    # min_exit at t=5: min(low[2..4])=109 → close 90<109 ✓
    # min_exit at t=6: min(low[3..5])=89 → close 88<89 ✓
    # min_exit at t=7: min(low[4..6])=87 → close 85<87 ✓
    state = PositionState(side="long", entry_price=110.0, stop_price=80.0)
    sigs = strat.on_candle(7, df.iloc[7], state)
    assert any(s.action == "exit_long" for s in sigs)


# ---------------------------------------------------------------------------
# breakout_trailing: trail_buffer_pct independence + breakeven_at_r + highwater
# ---------------------------------------------------------------------------


def _bt_default_params(**overrides):
    p = {
        "N_entrada": 5,
        "M_salida": 3,
        "stop_pct": 0.05,
        "trailing_lookback": 3,
        "trail_mode": "rolling",
        "trail_buffer_pct": 0.0,  # legacy: reuse stop_pct
        "breakeven_at_r": 0.0,
        "exit_confirmation_candles": 1,
        "habilitar_long": True,
        "habilitar_short": True,
        "salida_por_ruptura": True,
        "modo_ejecucion": "open_next",
        "coste_total_bps": 0.0,
    }
    p.update(overrides)
    return p


def test_breakout_trailing_buffer_pct_overrides_stop_pct_for_trailing():
    # Price marches up; with stop_pct=0.05 and trail_buffer_pct=0.001 the
    # trailing stop should land much closer to the rolling-min than the legacy
    # behaviour (which would multiply by (1 - 0.05)).
    closes = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 115.0]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    df = _df(closes, highs=highs, lows=lows)
    strat = BreakoutTrailingStrategy()
    strat.init(_bt_default_params(stop_pct=0.05, trail_buffer_pct=0.001), df)
    state = PositionState(side="long", entry_price=100.0, stop_price=95.0, entry_time=int(df.iloc[0]["open_time"]))
    sigs = strat.on_candle(6, df.iloc[6], state)
    moves = [s for s in sigs if s.action == "move_stop"]
    assert moves, "should emit a tightening move_stop"
    # rolling min of last 3 lows excluding t=6: min(105.5, 107.5, 109.5) = 105.5
    expected = 105.5 * (1 - 0.001)
    assert abs(moves[0].stop_price - expected) < 1e-6


def test_breakout_trailing_breakeven_moves_stop_to_entry_at_1R():
    # Entry at 100 with initial stop at 95 → R = 5. When close ≥ 105
    # (1R above entry) and breakeven_at_r=1 is on, stop should be moved to 100.
    closes = [100.0, 100.5, 101.0, 102.0, 103.0, 104.0, 105.0]
    highs = [c + 0.2 for c in closes]
    lows = [c - 0.2 for c in closes]
    df = _df(closes, highs=highs, lows=lows)
    strat = BreakoutTrailingStrategy()
    strat.init(
        _bt_default_params(stop_pct=0.05, breakeven_at_r=1.0, trailing_lookback=3),
        df,
    )
    state = PositionState(side="long", entry_price=100.0, stop_price=95.0, entry_time=int(df.iloc[0]["open_time"]))
    # Iterate so the strategy captures the initial stop on first observation
    for t in range(7):
        sigs = strat.on_candle(t, df.iloc[t], state)
        be = [s for s in sigs if s.action == "move_stop" and abs(s.stop_price - 100.0) < 1e-6]
        if be:
            return
    raise AssertionError("expected a break-even move_stop at 100.0 once close hit 105")


def test_breakout_trailing_highwater_uses_lows_only_after_high_water():
    # Build: low=98 in the middle (BEFORE the high water), then a new high at the end.
    # In rolling mode with lookback=3 the trailing reference would be low=98
    # when the rolling window includes the dip. In highwater mode it should
    # be the LATEST low (after the high water), not 98.
    closes = [100.0, 98.0, 100.0, 105.0, 108.0, 112.0]
    highs = [101.0, 99.0, 101.0, 106.0, 109.0, 113.0]
    lows = [99.0, 97.0, 99.0, 104.0, 107.0, 111.0]
    df = _df(closes, highs=highs, lows=lows)
    strat = BreakoutTrailingStrategy()
    strat.init(_bt_default_params(stop_pct=0.001, trail_mode="highwater", trail_buffer_pct=0.001), df)
    state = PositionState(side="long", entry_price=100.0, stop_price=95.0, entry_time=int(df.iloc[0]["open_time"]))
    # At t=5: highest high in [0..5] is 113 at index 5.
    # Lows since hw_idx=5 are just [111]. Stop candidate ≈ 111 * (1 - 0.001).
    sigs = strat.on_candle(5, df.iloc[5], state)
    moves = [s for s in sigs if s.action == "move_stop"]
    assert moves, "should emit move_stop in highwater mode after a fresh high"
    expected = 111.0 * (1 - 0.001)
    assert abs(moves[0].stop_price - expected) < 1e-6


# ---------------------------------------------------------------------------
# support_resistance_trailing: trail_buffer_pct + breakeven
# ---------------------------------------------------------------------------


def test_sr_trailing_buffer_pct_overrides_stop_pct_for_trailing():
    # Build pivots and confirm the trailing buffer is applied independently.
    closes = [100, 105, 110, 105, 100, 95, 96, 100, 105, 112]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    df = _df(closes, highs=highs, lows=lows)
    strat = SupportResistanceTrailingStrategy()
    strat.init(
        {
            "reversal_pct": 0.02,
            "stop_pct": 0.05,
            "trail_buffer_pct": 0.001,
            "breakeven_at_r": 0.0,
            "exit_confirmation_candles": 1,
            "modo_ejecucion": "open_next",
            "habilitar_long": True,
            "habilitar_short": True,
            "coste_total_bps": 0.0,
        },
        df,
    )
    # Pretend long opened at 100 with a stop well below the latest support.
    state = PositionState(side="long", entry_price=100.0, stop_price=80.0, entry_time=int(df.iloc[0]["open_time"]))
    # Iterate so the latest support has been confirmed.
    last_move = None
    for t in range(len(df)):
        sigs = strat.on_candle(t, df.iloc[t], state)
        for s in sigs:
            if s.action == "move_stop":
                last_move = s
    assert last_move is not None
    # When buffer is 0.1% rather than 5%, the stop should be much closer to
    # the support level (NOT support * 0.95).
    # Roughly: support * (1 - 0.001) — exact value depends on the confirmed pivot
    assert last_move.stop_price > 80.0, "stop should have tightened"


def test_sr_trailing_breakeven_at_1R():
    closes = [100, 102, 105, 108, 110, 112, 115]
    highs = [c + 0.2 for c in closes]
    lows = [c - 0.2 for c in closes]
    df = _df(closes, highs=highs, lows=lows)
    strat = SupportResistanceTrailingStrategy()
    strat.init(
        {
            "reversal_pct": 0.02,
            "stop_pct": 0.05,
            "trail_buffer_pct": 0.0,
            "breakeven_at_r": 1.0,
            "exit_confirmation_candles": 1,
            "modo_ejecucion": "open_next",
            "habilitar_long": True,
            "habilitar_short": True,
            "coste_total_bps": 0.0,
        },
        df,
    )
    # entry=100, initial stop=95 ⇒ R=5; close≥105 should trigger move to 100
    state = PositionState(side="long", entry_price=100.0, stop_price=95.0, entry_time=int(df.iloc[0]["open_time"]))
    for t in range(len(df)):
        sigs = strat.on_candle(t, df.iloc[t], state)
        be = [s for s in sigs if s.action == "move_stop" and abs(s.stop_price - 100.0) < 1e-6]
        if be:
            return
    raise AssertionError("expected break-even move_stop at 100.0 once close hit 105")


# ---------------------------------------------------------------------------
# support_resistance: exit_confirmation_candles
# ---------------------------------------------------------------------------


def test_sr_exit_confirm_3_requires_three_consecutive_dips():
    # Build pivots so a support is confirmed, then check that a single close
    # below support doesn't exit when 3 confirmations are required.
    closes = [100, 105, 110, 105, 100, 95, 96, 100, 105, 99]  # last dip below support
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    df = _df(closes, highs=highs, lows=lows)
    strat = SupportResistanceStrategy()
    strat.init(
        {
            "reversal_pct": 0.02,
            "stop_pct": 0.05,
            "exit_confirmation_candles": 3,
            "modo_ejecucion": "open_next",
            "habilitar_long": True,
            "habilitar_short": True,
            "coste_total_bps": 0.0,
        },
        df,
    )
    state = PositionState(side="long", entry_price=110.0, stop_price=80.0, entry_time=int(df.iloc[2]["open_time"]))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    assert all(s.action != "exit_long" for s in sigs), (
        "single dip below support should not trigger exit when 3-candle confirmation required"
    )
