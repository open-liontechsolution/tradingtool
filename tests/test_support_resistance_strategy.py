"""Tests for SupportResistanceStrategy: zigzag computation, entry/exit/stop signals."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.download_engine import INTERVAL_MS
from backend.strategies.base import PositionState
from backend.strategies.support_resistance import SupportResistanceStrategy

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


def _run_strategy(closes, highs=None, lows=None, opens=None, params=None):
    """Run strategy on given OHLCV data, return (strategy, df)."""
    df = _make_df(closes, highs, lows, opens)
    strat = SupportResistanceStrategy()
    default_params = {
        "reversal_pct": 0.05,
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
# Zigzag computation
# ---------------------------------------------------------------------------


class TestZigzag:
    def test_no_signals_on_flat_data(self):
        """Flat prices should not produce confirmed support+resistance pair."""
        closes = [100.0] * 20
        strat, df = _run_strategy(closes)
        # With flat data, no reversal occurs -> at least one level stays NaN
        state = PositionState()
        for t in range(20):
            signals = strat.on_candle(t, df.iloc[t], state)
            assert signals == []

    def test_zigzag_detects_resistance_after_drop(self):
        """Price rises then drops > reversal_pct -> resistance confirmed."""
        # Price goes up to 110, then drops to 100 (>5% reversal from 110)
        highs = [100, 105, 110, 110, 108, 105, 100, 95, 95, 95]
        lows = [99, 104, 109, 109, 107, 104, 99, 94, 94, 94]
        closes = [100, 105, 110, 110, 108, 105, 100, 95, 95, 95]
        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05})
        # Resistance should be confirmed at 110 once price drops to ~104.5 or below
        # At t=6 (low=99), resistance=110 should be confirmed
        assert not np.isnan(strat.last_resistance[6])
        assert strat.last_resistance[6] == 110.0

    def test_zigzag_detects_support_after_rise(self):
        """Price drops then rises > reversal_pct -> support confirmed."""
        # Price drops to 90, then rises to 100 (>5% reversal from 90)
        highs = [101, 96, 91, 91, 93, 96, 101, 106, 106, 106]
        lows = [100, 95, 90, 90, 92, 95, 100, 105, 105, 105]
        closes = [100, 95, 90, 90, 92, 95, 100, 105, 105, 105]
        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05})
        # The first direction is "up"; price goes down so current_high stays at 101.
        # At some point low drops enough: low=90 vs high=101 -> 101*(1-0.05)=95.95,
        # low=90 < 95.95 -> resistance confirmed at 101, direction switches to down.
        # Then price rises: high=106 vs low=90 -> 90*(1+0.05)=94.5,
        # high=93 at t=4 is not enough, high=96 at t=5 >= 94.5 -> support confirmed at 90.
        assert not np.isnan(strat.last_support[5])
        assert strat.last_support[5] == 90.0

    def test_zigzag_alternates(self):
        """Support and resistance should alternate."""
        # Up -> down -> up pattern
        highs = [101, 106, 111, 111, 106, 101, 96, 96, 101, 106, 111, 116]
        lows = [99, 104, 109, 109, 104, 99, 94, 94, 99, 104, 109, 114]
        closes = [100, 105, 110, 110, 105, 100, 95, 95, 100, 105, 110, 115]
        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05})
        # By the end, both support and resistance should be confirmed
        last_t = len(closes) - 1
        assert not np.isnan(strat.last_support[last_t])
        assert not np.isnan(strat.last_resistance[last_t])


# ---------------------------------------------------------------------------
# Entry signals
# ---------------------------------------------------------------------------


class TestEntrySignals:
    def _make_sr_data(self):
        """Create data with known support=90, resistance=110, then a breakout candle."""
        # Phase 1: establish resistance at 110 (rise to 110, drop > 5%)
        highs_1 = [101, 106, 111, 111, 106, 101]
        lows_1 = [99, 104, 109, 109, 104, 99]
        closes_1 = [100, 105, 110, 110, 105, 100]
        # Phase 2: establish support at 89 (drop to 89, rise > 5%)
        highs_2 = [96, 91, 90, 90, 95, 100]
        lows_2 = [94, 89, 89, 89, 93, 98]
        closes_2 = [95, 90, 89, 89, 94, 99]
        # Phase 3: breakout candle
        highs = highs_1 + highs_2
        lows = lows_1 + lows_2
        closes = closes_1 + closes_2
        return closes, highs, lows

    def test_entry_long_on_breakout_above_resistance(self):
        """Close > last_resistance should generate entry_long when flat."""
        closes, highs, lows = self._make_sr_data()
        # Add a breakout candle above resistance
        closes.append(115.0)
        highs.append(116.0)
        lows.append(114.0)

        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05})

        t = len(closes) - 1
        resistance = strat.last_resistance[t]
        # Verify resistance is confirmed and close > resistance
        assert not np.isnan(resistance)
        assert closes[-1] > resistance

        state = PositionState()
        signals = strat.on_candle(t, df.iloc[t], state)
        actions = [s.action for s in signals]
        assert "entry_long" in actions

    def test_entry_short_on_breakout_below_support(self):
        """Close < last_support should generate entry_short when flat."""
        closes, highs, lows = self._make_sr_data()
        # Add a breakdown candle below support
        closes.append(80.0)
        highs.append(81.0)
        lows.append(79.0)

        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05})

        t = len(closes) - 1
        support = strat.last_support[t]
        assert not np.isnan(support)
        assert closes[-1] < support

        state = PositionState()
        signals = strat.on_candle(t, df.iloc[t], state)
        actions = [s.action for s in signals]
        assert "entry_short" in actions

    def test_no_entry_long_when_disabled(self):
        closes, highs, lows = self._make_sr_data()
        closes.append(115.0)
        highs.append(116.0)
        lows.append(114.0)

        strat, df = _run_strategy(
            closes, highs=highs, lows=lows, params={"reversal_pct": 0.05, "habilitar_long": False}
        )

        t = len(closes) - 1
        state = PositionState()
        signals = strat.on_candle(t, df.iloc[t], state)
        assert not any(s.action == "entry_long" for s in signals)

    def test_no_entry_short_when_disabled(self):
        closes, highs, lows = self._make_sr_data()
        closes.append(80.0)
        highs.append(81.0)
        lows.append(79.0)

        strat, df = _run_strategy(
            closes, highs=highs, lows=lows, params={"reversal_pct": 0.05, "habilitar_short": False}
        )

        t = len(closes) - 1
        state = PositionState()
        signals = strat.on_candle(t, df.iloc[t], state)
        assert not any(s.action == "entry_short" for s in signals)

    def test_no_entry_when_position_open(self):
        """Should not generate entry signal when already in a position."""
        closes, highs, lows = self._make_sr_data()
        closes.append(115.0)
        highs.append(116.0)
        lows.append(114.0)

        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05})

        t = len(closes) - 1
        state = PositionState(side="long", entry_price=100.0, quantity=1.0, stop_price=80.0)
        signals = strat.on_candle(t, df.iloc[t], state)
        assert not any(s.action in ("entry_long", "entry_short") for s in signals)

    def test_stop_price_correct_for_long(self):
        """Stop for long entry = last_support * (1 - stop_pct)."""
        closes, highs, lows = self._make_sr_data()
        closes.append(115.0)
        highs.append(116.0)
        lows.append(114.0)

        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05, "stop_pct": 0.10})

        t = len(closes) - 1
        state = PositionState()
        signals = strat.on_candle(t, df.iloc[t], state)
        entry = next((s for s in signals if s.action == "entry_long"), None)
        assert entry is not None
        support = strat.last_support[t]
        expected_stop = support * (1.0 - 0.10)
        assert abs(entry.stop_price - expected_stop) < 1e-6

    def test_stop_price_correct_for_short(self):
        """Stop for short entry = last_resistance * (1 + stop_pct)."""
        closes, highs, lows = self._make_sr_data()
        closes.append(80.0)
        highs.append(81.0)
        lows.append(79.0)

        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05, "stop_pct": 0.10})

        t = len(closes) - 1
        state = PositionState()
        signals = strat.on_candle(t, df.iloc[t], state)
        entry = next((s for s in signals if s.action == "entry_short"), None)
        assert entry is not None
        resistance = strat.last_resistance[t]
        expected_stop = resistance * (1.0 + 0.10)
        assert abs(entry.stop_price - expected_stop) < 1e-6


# ---------------------------------------------------------------------------
# Exit signals
# ---------------------------------------------------------------------------


class TestExitSignals:
    def test_exit_long_when_close_below_support(self):
        """In long position, exit when Close < last_support."""
        closes, highs, lows = self._make_sr_data()
        # Add candle that breaks below support
        closes.append(85.0)
        highs.append(86.0)
        lows.append(84.5)  # above stop so stop doesn't fire

        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05})

        t = len(closes) - 1
        support = strat.last_support[t]
        assert not np.isnan(support)
        assert closes[-1] < support

        state = PositionState(side="long", entry_price=100.0, stop_price=50.0, quantity=1.0)
        signals = strat.on_candle(t, df.iloc[t], state)
        assert any(s.action == "exit_long" for s in signals)

    def _make_sr_data(self):
        """Same helper as in TestEntrySignals."""
        highs_1 = [101, 106, 111, 111, 106, 101]
        lows_1 = [99, 104, 109, 109, 104, 99]
        closes_1 = [100, 105, 110, 110, 105, 100]
        highs_2 = [96, 91, 90, 90, 95, 100]
        lows_2 = [94, 89, 89, 89, 93, 98]
        closes_2 = [95, 90, 89, 89, 94, 99]
        highs = highs_1 + highs_2
        lows = lows_1 + lows_2
        closes = closes_1 + closes_2
        return closes, highs, lows

    def test_exit_short_when_close_above_resistance(self):
        """In short position, exit when Close > last_resistance."""
        closes, highs, lows = self._make_sr_data()
        # Add candle that breaks above resistance
        closes.append(115.0)
        highs.append(115.5)  # below stop so stop doesn't fire
        lows.append(114.0)

        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05})

        t = len(closes) - 1
        resistance = strat.last_resistance[t]
        assert not np.isnan(resistance)
        assert closes[-1] > resistance

        state = PositionState(side="short", entry_price=100.0, stop_price=200.0, quantity=1.0)
        signals = strat.on_candle(t, df.iloc[t], state)
        assert any(s.action == "exit_short" for s in signals)


# ---------------------------------------------------------------------------
# Stop loss signals
# ---------------------------------------------------------------------------


class TestStopLossSignals:
    def _make_sr_data(self):
        highs_1 = [101, 106, 111, 111, 106, 101]
        lows_1 = [99, 104, 109, 109, 104, 99]
        closes_1 = [100, 105, 110, 110, 105, 100]
        highs_2 = [96, 91, 90, 90, 95, 100]
        lows_2 = [94, 89, 89, 89, 93, 98]
        closes_2 = [95, 90, 89, 89, 94, 99]
        highs = highs_1 + highs_2
        lows = lows_1 + lows_2
        closes = closes_1 + closes_2
        return closes, highs, lows

    def test_stop_long_triggered_by_low(self):
        """Stop for long triggered when Low <= stop_price."""
        closes, highs, lows = self._make_sr_data()
        closes.append(100.0)
        highs.append(101.0)
        lows.append(69.0)  # below stop_price=70

        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05})

        t = len(closes) - 1
        state = PositionState(side="long", entry_price=100.0, stop_price=70.0, quantity=1.0)
        signals = strat.on_candle(t, df.iloc[t], state)
        assert any(s.action == "stop_long" for s in signals)

    def test_stop_short_triggered_by_high(self):
        """Stop for short triggered when High >= stop_price."""
        closes, highs, lows = self._make_sr_data()
        closes.append(100.0)
        highs.append(131.0)  # above stop_price=130
        lows.append(99.0)

        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05})

        t = len(closes) - 1
        state = PositionState(side="short", entry_price=100.0, stop_price=130.0, quantity=1.0)
        signals = strat.on_candle(t, df.iloc[t], state)
        assert any(s.action == "stop_short" for s in signals)

    def test_stop_takes_priority_over_exit(self):
        """When both stop and exit conditions are met, stop is returned (returns early)."""
        closes, highs, lows = self._make_sr_data()
        closes.append(80.0)  # below support -> exit condition
        highs.append(81.0)
        lows.append(69.0)  # below stop_price=70 -> stop condition

        strat, df = _run_strategy(closes, highs=highs, lows=lows, params={"reversal_pct": 0.05})

        t = len(closes) - 1
        state = PositionState(side="long", entry_price=100.0, stop_price=70.0, quantity=1.0)
        signals = strat.on_candle(t, df.iloc[t], state)
        assert signals[0].action == "stop_long"
        assert len(signals) == 1  # only stop, not also exit


# ---------------------------------------------------------------------------
# Parameter definitions
# ---------------------------------------------------------------------------


class TestParameterDefs:
    def test_get_parameters_returns_all(self):
        strat = SupportResistanceStrategy()
        params = strat.get_parameters()
        names = {p.name for p in params}
        expected = {
            "reversal_pct",
            "stop_pct",
            "modo_ejecucion",
            "habilitar_long",
            "habilitar_short",
            "coste_total_bps",
        }
        assert expected == names

    def test_strategy_name(self):
        assert SupportResistanceStrategy.name == "support_resistance"

    def test_no_candle_count_params(self):
        """This strategy should NOT have N_entrada or M_salida."""
        strat = SupportResistanceStrategy()
        params = strat.get_parameters()
        names = {p.name for p in params}
        assert "N_entrada" not in names
        assert "M_salida" not in names
