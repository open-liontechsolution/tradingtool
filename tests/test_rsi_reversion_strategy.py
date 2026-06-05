"""Tests for RsiReversionStrategy: Connors-style RSI dip entry with a trend
filter, several exit modes (rsi/sma/target/first_up), wide catastrophic stop,
and an optional max_hold time stop."""

from __future__ import annotations

import pandas as pd

from backend.download_engine import INTERVAL_MS
from backend.strategies.base import PositionState
from backend.strategies.rsi_reversion import RsiReversionStrategy


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


def _params(**overrides):
    p = {
        "trend_sma_n": 200,
        "rsi_period": 3,
        "rsi_entry_long": 20.0,
        "rsi_entry_short": 80.0,
        "exit_mode": "target",
        "rsi_exit_long": 60.0,
        "rsi_exit_short": 40.0,
        "exit_sma_n": 5,
        "target_pct": 0.05,
        "stop_pct": 0.15,
        "max_hold": 0,
        "habilitar_long": True,
        "habilitar_short": False,
        "modo_ejecucion": "open_next",
        "coste_total_bps": 0.0,
    }
    p.update(overrides)
    return p


def _run(closes, highs=None, lows=None, opens=None, params=None):
    df = _make_df(closes, highs, lows, opens)
    strat = RsiReversionStrategy()
    strat.init(params or _params(), df)
    return strat, df


# ---------------------------------------------------------------------------
# Parameter definition / validated defaults
# ---------------------------------------------------------------------------


def test_get_parameters_returns_expected_names():
    names = {p.name for p in RsiReversionStrategy().get_parameters()}
    for expected in ("trend_sma_n", "rsi_period", "rsi_entry_long", "exit_mode", "target_pct", "stop_pct", "max_hold"):
        assert expected in names


def test_validated_defaults():
    # The defaults are the cross-symbol robust config (≥65% win-rate, profitable
    # on BTC/ETH/SOL on 4h and 1d). Guard them against accidental edits.
    p = {x.name: x.default for x in RsiReversionStrategy().get_parameters()}
    assert p["trend_sma_n"] == 200
    assert p["rsi_period"] == 3
    assert p["rsi_entry_long"] == 20.0
    assert p["exit_mode"] == "target"
    assert p["target_pct"] == 0.05
    assert p["stop_pct"] == 0.15
    assert p["habilitar_long"] is True
    assert p["habilitar_short"] is False


# ---------------------------------------------------------------------------
# Warm-up
# ---------------------------------------------------------------------------


def test_no_entry_before_trend_sma_warmup():
    # Fewer candles than trend_sma_n → SMA is NaN → entry gate returns early.
    strat, df = _run([100.0] * 5, params=_params(trend_sma_n=10))
    state = PositionState()
    for t in range(len(df)):
        assert all(s.action != "entry_long" for s in strat.on_candle(t, df.iloc[t], state))


# ---------------------------------------------------------------------------
# Entry: oversold RSI dip (trend filter disabled to isolate the RSI gate)
# ---------------------------------------------------------------------------


def test_long_entry_on_oversold_dip():
    # Sustained drop at the end drives RSI(3) deep into oversold.
    closes = [100.0] * 5 + [96.0, 92.0, 88.0]
    strat, df = _run(closes, params=_params(trend_sma_n=0, rsi_period=3, rsi_entry_long=30.0))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], PositionState())
    entries = [s for s in sigs if s.action == "entry_long"]
    assert entries, "expected long entry on oversold RSI dip"
    # Stop is placed stop_pct below the entry close.
    assert entries[0].stop_price == 88.0 * (1.0 - 0.15)


def test_no_long_entry_when_rsi_not_oversold():
    # A steady uptrend keeps RSI(3) near 100, well above the oversold gate.
    closes = [90.0, 92.0, 94.0, 96.0, 98.0, 100.0, 102.0, 104.0]
    strat, df = _run(closes, params=_params(trend_sma_n=0, rsi_period=3, rsi_entry_long=10.0))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], PositionState())
    assert all(s.action != "entry_long" for s in sigs)


# ---------------------------------------------------------------------------
# Trend filter
# ---------------------------------------------------------------------------


def test_trend_filter_blocks_long_in_downtrend():
    # Permissive RSI gate (99) so ONLY the trend filter can block the entry.
    closes = [120.0, 115.0, 110.0, 105.0, 100.0, 95.0, 90.0]
    strat, df = _run(closes, params=_params(trend_sma_n=5, rsi_entry_long=99.0))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], PositionState())
    assert all(s.action != "entry_long" for s in sigs), "close < SMA(trend) must block long"


def test_trend_filter_allows_long_in_uptrend():
    # Uptrend with a final dip; close stays above SMA(trend). Permissive RSI gate.
    closes = [60.0, 65.0, 70.0, 75.0, 80.0, 85.0, 90.0, 88.0]
    strat, df = _run(closes, params=_params(trend_sma_n=5, rsi_entry_long=99.0))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], PositionState())
    assert any(s.action == "entry_long" for s in sigs), "close > SMA(trend) should permit long"


# ---------------------------------------------------------------------------
# Exits
# ---------------------------------------------------------------------------


def test_target_exit_fires_when_close_reaches_target():
    closes = [100.0] * 6 + [106.0]  # +6% > 5% target
    strat, df = _run(closes, params=_params(exit_mode="target", target_pct=0.05))
    state = PositionState(side="long", entry_price=100.0, stop_price=85.0, entry_time=int(df.iloc[0]["open_time"]))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    assert any(s.action == "exit_long" for s in sigs)


def test_no_target_exit_before_target_reached():
    closes = [100.0] * 6 + [103.0]  # +3% < 5% target
    strat, df = _run(closes, params=_params(exit_mode="target", target_pct=0.05))
    state = PositionState(side="long", entry_price=100.0, stop_price=85.0, entry_time=int(df.iloc[0]["open_time"]))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    assert all(s.action != "exit_long" for s in sigs)


def test_stop_hit_when_low_breaches_stop():
    closes = [100.0] * 7
    lows = [99.0] * 6 + [80.0]
    strat, df = _run(closes, lows=lows, params=_params())
    state = PositionState(side="long", entry_price=100.0, stop_price=85.0, entry_time=int(df.iloc[0]["open_time"]))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    assert any(s.action == "stop_long" for s in sigs)


def test_max_hold_time_stop_forces_exit():
    # Flat price: no target, no stop — only the time stop can close the trade.
    closes = [100.0] * 8
    strat, df = _run(closes, params=_params(exit_mode="target", target_pct=0.5, max_hold=2))
    entry_idx = 2
    state = PositionState(
        side="long", entry_price=100.0, stop_price=50.0, entry_time=int(df.iloc[entry_idx]["open_time"])
    )
    # Not yet held long enough.
    assert all(s.action != "exit_long" for s in strat.on_candle(entry_idx + 1, df.iloc[entry_idx + 1], state))
    # held == max_hold → exit.
    assert any(s.action == "exit_long" for s in strat.on_candle(entry_idx + 2, df.iloc[entry_idx + 2], state))


def test_sma_exit_mode_exits_above_exit_sma():
    closes = [90.0, 92.0, 94.0, 96.0, 98.0, 110.0]  # last close well above short SMA
    strat, df = _run(closes, params=_params(exit_mode="sma", exit_sma_n=3))
    state = PositionState(side="long", entry_price=90.0, stop_price=70.0, entry_time=int(df.iloc[0]["open_time"]))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    assert any(s.action == "exit_long" for s in sigs)


def test_first_up_exit_mode_exits_on_first_up_close():
    closes = [100.0, 99.0, 98.0, 99.5]  # final candle closes above the prior
    strat, df = _run(closes, params=_params(exit_mode="first_up"))
    state = PositionState(side="long", entry_price=100.0, stop_price=85.0, entry_time=int(df.iloc[0]["open_time"]))
    sigs = strat.on_candle(len(df) - 1, df.iloc[-1], state)
    assert any(s.action == "exit_long" for s in sigs)
