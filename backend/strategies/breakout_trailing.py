"""Breakout with trailing stop.

Inherits the entry/exit/initial-stop logic of BreakoutStrategy. Adds an extra
``move_stop`` signal that trails behind price.

Trailing modes:
- ``rolling`` (default, original behaviour): trail to the rolling Min/Max of
  the last ``trailing_lookback`` closed candles.
- ``highwater``: track the highest high (long) / lowest low (short) since the
  trade was opened, then trail to the lowest low (long) / highest high (short)
  observed since that water mark. Reacts faster than rolling on strong trends
  and stays out of older "noise" candles that pre-date the breakout.

Optional refinements (all default-off so existing configs keep their numbers):
- ``trail_buffer_pct`` lets the trailing buffer be set independently of
  ``stop_pct`` (which sizes the initial stop). Set to 0 to keep the legacy
  behaviour of reusing ``stop_pct`` for both.
- ``breakeven_at_r`` moves the stop to the entry price once unrealised PnL
  reaches N × initial-risk. Default 0 disables the rule.
"""

from __future__ import annotations

import pandas as pd

from backend.strategies.base import ParameterDef, PositionState, Signal
from backend.strategies.breakout import BreakoutStrategy


class BreakoutTrailingStrategy(BreakoutStrategy):
    name = "breakout_trailing"
    description = (
        "Breakout con trailing stop. Hereda entradas/salidas de 'breakout'. "
        "Soporta trailing rolling (Min/Max de las últimas N velas, original) "
        "o highwater (Chandelier-style desde el máximo del trade). Buffer y "
        "stop inicial pueden separarse, y se puede activar break-even al "
        "alcanzar N × R de profit no realizado."
    )

    def __init__(self) -> None:
        super().__init__()
        # Per-trade memory used to size break-even moves correctly.
        # Resets the first time we observe a new entry_time.
        self._initial_stop_per_entry: tuple[int, float] | None = None

    def get_parameters(self) -> list[ParameterDef]:
        params = super().get_parameters()
        params.extend(
            [
                ParameterDef(
                    "trailing_lookback",
                    "int",
                    10,
                    1,
                    500,
                    "Lookback (closed candles) for the trailing reference. Used by trail_mode='rolling'.",
                ),
                ParameterDef(
                    "trail_mode",
                    "str",
                    "rolling",
                    None,
                    None,
                    "Trailing reference mechanism: 'rolling' (Min/Max of last trailing_lookback velas) or 'highwater' (lows since the highest high of the trade)",
                ),
                ParameterDef(
                    "trail_buffer_pct",
                    "float",
                    0.0,
                    0.0,
                    0.5,
                    "Trailing buffer below the new low (long) / above the new high (short). 0 = reuse stop_pct (legacy behaviour).",
                ),
                ParameterDef(
                    "breakeven_at_r",
                    "float",
                    0.0,
                    0.0,
                    10.0,
                    "Move stop to entry price when unrealised PnL reaches N × initial risk. 0 disables (legacy).",
                ),
            ]
        )
        return params

    def init(self, params: dict, candles: pd.DataFrame) -> None:
        super().init(params, candles)
        lookback = int(params.get("trailing_lookback", 10))
        self.trail_min = candles["low"].shift(1).rolling(lookback).min()
        self.trail_max = candles["high"].shift(1).rolling(lookback).max()
        # Reset per-trade memory at every fresh init (one per backtest run; in
        # live the strategy is re-instantiated per scan cycle).
        self._initial_stop_per_entry = None

    def _capture_initial_stop(self, state: PositionState) -> float:
        """Return the initial stop for the current trade. Memoised by entry_time
        so trailing updates don't overwrite the reference used for break-even."""
        if state.side == "flat":
            return 0.0
        prev = self._initial_stop_per_entry
        if prev is None or prev[0] != state.entry_time:
            self._initial_stop_per_entry = (state.entry_time, state.stop_price)
            return state.stop_price
        return prev[1]

    def _highwater_low_since_entry(self, t: int, state: PositionState) -> float:
        """For longs: lowest low observed since the highest high of the trade."""
        # Find entry index (open_time match); the engine guarantees it exists.
        candles = self.candles
        # Slice from entry candle through current candle inclusive.
        entry_mask = candles["open_time"] == state.entry_time
        if not entry_mask.any():
            return float("nan")
        entry_idx = int(candles.index[entry_mask][0])
        if entry_idx > t:
            return float("nan")
        window = candles.iloc[entry_idx : t + 1]
        hw_pos_in_window = int(window["high"].values.argmax())
        # Lows from the high-water bar onward (inclusive of the HW bar itself).
        lows_after_hw = window.iloc[hw_pos_in_window:]["low"]
        return float(lows_after_hw.min())

    def _highwater_high_since_entry(self, t: int, state: PositionState) -> float:
        """For shorts: highest high observed since the lowest low of the trade."""
        candles = self.candles
        entry_mask = candles["open_time"] == state.entry_time
        if not entry_mask.any():
            return float("nan")
        entry_idx = int(candles.index[entry_mask][0])
        if entry_idx > t:
            return float("nan")
        window = candles.iloc[entry_idx : t + 1]
        lw_pos_in_window = int(window["low"].values.argmin())
        highs_after_lw = window.iloc[lw_pos_in_window:]["high"]
        return float(highs_after_lw.max())

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        signals = super().on_candle(t, candle, state)

        # Base class returns early on stop/exit; no trailing in that case.
        if state.side == "flat" or any(
            s.action in ("stop_long", "stop_short", "exit_long", "exit_short") for s in signals
        ):
            return signals

        params = self.params
        stop_pct = float(params.get("stop_pct", 0.02))
        trail_mode = str(params.get("trail_mode", "rolling")).lower()
        trail_buffer = float(params.get("trail_buffer_pct", 0.0))
        if trail_buffer <= 0.0:
            trail_buffer = stop_pct  # legacy: reuse stop_pct
        breakeven_at_r = float(params.get("breakeven_at_r", 0.0))

        initial_stop = self._capture_initial_stop(state)

        # Optional break-even move (applied first; trailing may still tighten further below)
        if breakeven_at_r > 0.0 and initial_stop > 0.0 and state.entry_price > 0.0:
            close_price = float(candle["close"])
            if state.side == "long":
                R = state.entry_price - initial_stop
                if (
                    R > 0
                    and close_price >= state.entry_price + breakeven_at_r * R
                    and state.entry_price > state.stop_price
                ):
                    signals.append(Signal(action="move_stop", stop_price=state.entry_price))
            elif state.side == "short":
                R = initial_stop - state.entry_price
                if (
                    R > 0
                    and close_price <= state.entry_price - breakeven_at_r * R
                    and state.entry_price < state.stop_price
                ):
                    signals.append(Signal(action="move_stop", stop_price=state.entry_price))

        # Determine trailing reference based on mode
        if state.side == "long":
            ref = self._highwater_low_since_entry(t, state) if trail_mode == "highwater" else self.trail_min.iloc[t]
            if pd.isna(ref):
                return signals
            candidate = float(ref) * (1.0 - trail_buffer)
            existing_moves = [s.stop_price for s in signals if s.action == "move_stop"]
            current_target = max([state.stop_price] + existing_moves)
            if candidate > current_target:
                signals.append(Signal(action="move_stop", stop_price=candidate))

        elif state.side == "short":
            ref = self._highwater_high_since_entry(t, state) if trail_mode == "highwater" else self.trail_max.iloc[t]
            if pd.isna(ref):
                return signals
            candidate = float(ref) * (1.0 + trail_buffer)
            existing_moves = [s.stop_price for s in signals if s.action == "move_stop"]
            current_target = min([state.stop_price] + existing_moves)
            if candidate < current_target:
                signals.append(Signal(action="move_stop", stop_price=candidate))

        return signals
