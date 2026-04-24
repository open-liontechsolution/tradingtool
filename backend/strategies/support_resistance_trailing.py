"""Support/Resistance with trailing stop.

Inherits the zigzag-based entries/exits of SupportResistanceStrategy. While a
position is open, emits a ``move_stop`` signal whenever a newly confirmed
support (long) or resistance (short) yields a tighter stop than the current
one, applying the same ``stop_pct`` buffer the base strategy uses at entry.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.strategies.base import PositionState, Signal
from backend.strategies.support_resistance import SupportResistanceStrategy


class SupportResistanceTrailingStrategy(SupportResistanceStrategy):
    name = "support_resistance_trailing"
    description = (
        "Soportes/Resistencias con trailing stop. Hereda entradas/salidas de "
        "'support_resistance'; cada vez que se confirma un nuevo soporte (long) "
        "o resistencia (short) más cercano al precio, mueve el stop al nivel "
        "soporte/resistencia × (1 ∓ stop_pct)."
    )

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        signals = super().on_candle(t, candle, state)

        if state.side == "flat" or any(
            s.action in ("stop_long", "stop_short", "exit_long", "exit_short") for s in signals
        ):
            return signals

        stop_pct = float(self.params.get("stop_pct", 0.02))
        support = self.last_support[t]
        resistance = self.last_resistance[t]

        if state.side == "long" and not np.isnan(support):
            candidate = float(support) * (1.0 - stop_pct)
            if candidate > state.stop_price:
                signals.append(Signal(action="move_stop", stop_price=candidate))

        elif state.side == "short" and not np.isnan(resistance):
            candidate = float(resistance) * (1.0 + stop_pct)
            if candidate < state.stop_price:
                signals.append(Signal(action="move_stop", stop_price=candidate))

        return signals
