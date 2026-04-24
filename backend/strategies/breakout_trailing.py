"""Breakout with trailing stop.

Inherits the entry/exit/initial-stop logic of BreakoutStrategy. Adds an extra
``move_stop`` signal that trails behind price using the rolling extreme of the
last ``trailing_lookback`` closed candles (Min for longs, Max for shorts),
with the same ``stop_pct`` buffer that the base strategy applies at entry.
"""

from __future__ import annotations

import pandas as pd

from backend.strategies.base import ParameterDef, PositionState, Signal
from backend.strategies.breakout import BreakoutStrategy


class BreakoutTrailingStrategy(BreakoutStrategy):
    name = "breakout_trailing"
    description = (
        "Breakout con trailing stop. Hereda entradas/salidas de 'breakout'; "
        "mueve el stop detrás del precio usando el Min/Max de las últimas "
        "trailing_lookback velas cerradas (con el buffer stop_pct)."
    )

    def get_parameters(self) -> list[ParameterDef]:
        params = super().get_parameters()
        params.append(
            ParameterDef(
                "trailing_lookback",
                "int",
                10,
                1,
                500,
                "Lookback (in closed candles) for the trailing Min/Max reference level",
            )
        )
        return params

    def init(self, params: dict, candles: pd.DataFrame) -> None:
        super().init(params, candles)
        lookback = int(params.get("trailing_lookback", 10))
        # Rolling extremes over the last `lookback` closed candles (exclusive of t).
        self.trail_min = candles["low"].shift(1).rolling(lookback).min()
        self.trail_max = candles["high"].shift(1).rolling(lookback).max()

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        signals = super().on_candle(t, candle, state)

        # Base class returns early on stop/exit; no trailing in that case.
        # Only emit move_stop when a position is open and base didn't close it.
        if state.side == "flat" or any(
            s.action in ("stop_long", "stop_short", "exit_long", "exit_short") for s in signals
        ):
            return signals

        stop_pct = float(self.params.get("stop_pct", 0.02))

        if state.side == "long":
            ref = self.trail_min.iloc[t]
            if pd.isna(ref):
                return signals
            candidate = float(ref) * (1.0 - stop_pct)
            if candidate > state.stop_price:
                signals.append(Signal(action="move_stop", stop_price=candidate))

        elif state.side == "short":
            ref = self.trail_max.iloc[t]
            if pd.isna(ref):
                return signals
            candidate = float(ref) * (1.0 + stop_pct)
            if candidate < state.stop_price:
                signals.append(Signal(action="move_stop", stop_price=candidate))

        return signals
