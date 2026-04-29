"""Support/Resistance with trailing stop.

Inherits the zigzag-based entries/exits of SupportResistanceStrategy. While a
position is open, emits a ``move_stop`` signal whenever a newly confirmed
support (long) or resistance (short) yields a tighter stop than the current
one.

Optional refinements (default-off so existing configs preserve their numbers):
- ``trail_buffer_pct`` lets the trailing buffer be set independently of
  ``stop_pct`` (which sizes the initial stop). Set to 0 to keep the legacy
  behaviour of reusing ``stop_pct`` for both.
- ``breakeven_at_r`` moves the stop to the entry price once unrealised PnL
  reaches N × initial-risk. Default 0 disables the rule.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.strategies.base import ParameterDef, PositionState, Signal
from backend.strategies.support_resistance import SupportResistanceStrategy


class SupportResistanceTrailingStrategy(SupportResistanceStrategy):
    name = "support_resistance_trailing"
    description = (
        "Soportes/Resistencias con trailing stop. Hereda entradas/salidas de "
        "'support_resistance'; cada vez que se confirma un nuevo soporte (long) "
        "o resistencia (short) más cercano al precio, mueve el stop. Buffer "
        "del trailing puede separarse del stop_pct inicial, y se puede activar "
        "break-even al alcanzar N × R de profit no realizado."
    )

    def __init__(self) -> None:
        super().__init__()
        # Per-trade memory used to size break-even moves correctly.
        self._initial_stop_per_entry: tuple[int, float] | None = None

    def get_parameters(self) -> list[ParameterDef]:
        params = super().get_parameters()
        params.extend(
            [
                ParameterDef(
                    "trail_buffer_pct",
                    "float",
                    0.0,
                    0.0,
                    0.5,
                    "Trailing buffer below the new support (long) / above the new resistance (short). 0 = reuse stop_pct (legacy).",
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
        self._initial_stop_per_entry = None

    def _capture_initial_stop(self, state: PositionState) -> float:
        if state.side == "flat":
            return 0.0
        prev = self._initial_stop_per_entry
        if prev is None or prev[0] != state.entry_time:
            self._initial_stop_per_entry = (state.entry_time, state.stop_price)
            return state.stop_price
        return prev[1]

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        signals = super().on_candle(t, candle, state)

        if state.side == "flat" or any(
            s.action in ("stop_long", "stop_short", "exit_long", "exit_short") for s in signals
        ):
            return signals

        params = self.params
        stop_pct = float(params.get("stop_pct", 0.02))
        trail_buffer = float(params.get("trail_buffer_pct", 0.0))
        if trail_buffer <= 0.0:
            trail_buffer = stop_pct  # legacy
        breakeven_at_r = float(params.get("breakeven_at_r", 0.0))

        initial_stop = self._capture_initial_stop(state)

        # Optional break-even move
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

        support = self.last_support[t]
        resistance = self.last_resistance[t]

        if state.side == "long" and not np.isnan(support):
            candidate = float(support) * (1.0 - trail_buffer)
            existing_moves = [s.stop_price for s in signals if s.action == "move_stop"]
            current_target = max([state.stop_price] + existing_moves)
            if candidate > current_target:
                signals.append(Signal(action="move_stop", stop_price=candidate))

        elif state.side == "short" and not np.isnan(resistance):
            candidate = float(resistance) * (1.0 + trail_buffer)
            existing_moves = [s.stop_price for s in signals if s.action == "move_stop"]
            current_target = min([state.stop_price] + existing_moves)
            if candidate < current_target:
                signals.append(Signal(action="move_stop", stop_price=candidate))

        return signals
