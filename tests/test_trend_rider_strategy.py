"""Tests for TrendRiderStrategy: Donchian-breakout trend rider with a rising-SMA
regime filter, ADX gate, ATR initial stop, ATR chandelier trailing, and a
regime-flip exit. Long-only by default."""

from __future__ import annotations

import pandas as pd

from backend.download_engine import INTERVAL_MS
from backend.strategies.base import PositionState
from backend.strategies.trend_rider import TrendRiderStrategy


def _make_df(closes, highs=None, lows=None, opens=None) -> pd.DataFrame:
    n = len(closes)
    if highs is None:
        highs = [c + 0.5 for c in closes]
    if lows is None:
        lows = [c - 0.5 for c in closes]
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


def _params(**ov):
    p = {
        "donchian_n": 5,
        "donchian_exit_n": 5,
        "regime_sma_n": 5,
        "regime_slope_n": 2,
        "adx_period": 5,
        "adx_threshold": 10.0,
        "atr_period": 5,
        "atr_stop_mult": 3.0,
        "atr_trail_mult": 8.0,
        "exit_on_regime_flip": True,
        "salida_por_ruptura": False,
        "modo_ejecucion": "open_next",
        "habilitar_long": True,
        "habilitar_short": False,
        "coste_total_bps": 0.0,
    }
    p.update(ov)
    return p


def _run(closes, highs=None, lows=None, params=None):
    df = _make_df(closes, highs, lows)
    strat = TrendRiderStrategy()
    strat.init(params or _params(), df)
    return strat, df


# --- params / validated defaults ------------------------------------------


def test_get_parameters_names():
    names = {p.name for p in TrendRiderStrategy().get_parameters()}
    for n in ("donchian_n", "regime_sma_n", "regime_slope_n", "adx_threshold", "atr_stop_mult", "atr_trail_mult"):
        assert n in names


def test_validated_defaults():
    # The x10 recipe (SOL 1d, lev 3): guard the shipped defaults.
    p = {x.name: x.default for x in TrendRiderStrategy().get_parameters()}
    assert p["donchian_n"] == 20
    assert p["regime_sma_n"] == 100
    assert p["regime_slope_n"] == 40
    assert p["adx_threshold"] == 15.0
    assert p["atr_stop_mult"] == 3.0
    assert p["atr_trail_mult"] == 8.0
    assert p["habilitar_long"] is True
    assert p["habilitar_short"] is False


# --- entries ---------------------------------------------------------------


def test_long_entry_on_breakout_in_rising_regime():
    closes = [10.0 + i for i in range(24)]  # steady uptrend → rising SMA, high ADX, new highs
    strat, df = _run(closes)
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], PositionState())
    entries = [s for s in sigs if s.action == "entry_long"]
    assert entries, "expected long entry on breakout in a rising regime"
    # initial stop = close - atr_stop_mult * ATR (below entry)
    assert entries[0].stop_price < float(df.iloc[-1]["close"])


def test_no_long_in_downtrend():
    closes = [33.0 - i for i in range(24)]  # steady downtrend → SMA falling, below SMA
    strat, df = _run(closes)
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], PositionState())
    assert all(s.action != "entry_long" for s in sigs), "regime filter must block longs in a downtrend"


def test_regime_filter_off_allows_breakout_entry():
    # With regime gate disabled (regime_sma_n=0), a fresh high should enter even
    # without the rising-SMA confirmation.
    closes = [10.0 + i for i in range(24)]
    strat, df = _run(closes, params=_params(regime_sma_n=0))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], PositionState())
    assert any(s.action == "entry_long" for s in sigs)


def test_short_disabled_by_default():
    closes = [33.0 - i for i in range(24)]
    strat, df = _run(closes)  # habilitar_short False
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], PositionState())
    assert all(s.action != "entry_short" for s in sigs)


# --- warm-up ---------------------------------------------------------------


def test_no_entry_before_warmup():
    closes = [10.0 + i for i in range(6)]  # fewer candles than indicators need
    strat, df = _run(closes)
    for t in range(len(df)):
        assert all(s.action != "entry_long" for s in strat.on_candle(t, df.iloc[t], PositionState()))


# --- exits / trailing ------------------------------------------------------


def test_initial_stop_hit():
    closes = [20.0 + i for i in range(24)]
    lows = [c - 0.5 for c in closes]
    lows[-1] = 5.0  # pierce the stop
    strat, df = _run(closes, lows=lows)
    state = PositionState(side="long", entry_price=20.0, stop_price=18.0, entry_time=int(df.iloc[10]["open_time"]))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    assert any(s.action == "stop_long" for s in sigs)


def test_regime_flip_exit():
    # Uptrend then a sharp drop on the last close below the regime SMA.
    closes = [10.0 + i for i in range(20)] + [12.0]
    strat, df = _run(closes)
    state = PositionState(side="long", entry_price=15.0, stop_price=5.0, entry_time=int(df.iloc[15]["open_time"]))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    assert any(s.action == "exit_long" for s in sigs), "should exit long when close drops below the regime SMA"


def test_trailing_only_tightens():
    closes = [10.0 + i for i in range(24)]  # strong uptrend → trail well above a low stop
    strat, df = _run(closes)
    state = PositionState(side="long", entry_price=10.0, stop_price=11.0, entry_time=int(df.iloc[5]["open_time"]))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    moves = [s for s in sigs if s.action == "move_stop"]
    assert moves and moves[0].stop_price > 11.0, "trail should raise the stop in an uptrend"
