"""Breakout strategy: close-based breakout with percentage stop and exit on reversal."""
from __future__ import annotations

import pandas as pd

from backend.strategies.base import ParameterDef, PositionState, Signal, Strategy


class BreakoutStrategy(Strategy):
    name = "breakout"
    description = (
        "Breakout por cierre con stop porcentual y salida por ruptura. "
        "Entry when Close breaks above N-candle High (long) or below N-candle Low (short). "
        "Stop is placed at MinPrev*(1-stop_pct) for longs, MaxPrev*(1+stop_pct) for shorts. "
        "Exit when Close breaks below M-candle Low (long) or above M-candle High (short)."
    )

    def get_parameters(self) -> list[ParameterDef]:
        return [
            ParameterDef("N_entrada", "int", 20, 2, 500,
                         "Lookback window for breakout detection (exclusive of current candle)"),
            ParameterDef("M_salida", "int", 10, 1, 500,
                         "Lookback window for exit signal"),
            ParameterDef("stop_pct", "float", 0.02, 0.001, 0.5,
                         "Stop loss percentage from entry reference level"),
            ParameterDef("modo_ejecucion", "str", "open_next", None, None,
                         "Execution mode: 'open_next' or 'close_current'"),
            ParameterDef("habilitar_long", "bool", True, None, None,
                         "Enable long entries"),
            ParameterDef("habilitar_short", "bool", True, None, None,
                         "Enable short entries"),
            ParameterDef("coste_total_bps", "float", 10.0, 0.0, 100.0,
                         "Round-trip transaction cost in basis points"),
        ]

    def init(self, params: dict, candles: pd.DataFrame) -> None:
        self.params = params
        n = int(params.get("N_entrada", 20))
        m = int(params.get("M_salida", 10))

        # MaxPrev(t) = max(High) of N candles BEFORE t (exclusive)
        # Using shift(1) so that at time t we look at [t-N, t-1]
        self.max_prev = candles["high"].shift(1).rolling(n).max()
        self.min_prev = candles["low"].shift(1).rolling(n).min()

        # Exit levels: min/max of M candles before t (exclusive)
        self.min_exit = candles["low"].shift(1).rolling(m).min()
        self.max_exit = candles["high"].shift(1).rolling(m).max()

        self.candles = candles

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        params = self.params
        habilitar_long = bool(params.get("habilitar_long", True))
        habilitar_short = bool(params.get("habilitar_short", True))
        stop_pct = float(params.get("stop_pct", 0.02))

        signals: list[Signal] = []

        close = float(candle["close"])
        low = float(candle["low"])
        high = float(candle["high"])

        max_prev = self.max_prev.iloc[t]
        min_prev = self.min_prev.iloc[t]
        min_exit = self.min_exit.iloc[t]
        max_exit = self.max_exit.iloc[t]

        if pd.isna(max_prev) or pd.isna(min_prev) or pd.isna(min_exit) or pd.isna(max_exit):
            return signals

        if state.side == "long":
            # Check stop loss (intrabar: triggered on Low)
            if low <= state.stop_price:
                signals.append(Signal(action="stop_long", price=state.stop_price))
                return signals
            # Check exit on close
            if close < min_exit:
                signals.append(Signal(action="exit_long", price=close))
                return signals

        elif state.side == "short":
            # Check stop loss (intrabar: triggered on High)
            if high >= state.stop_price:
                signals.append(Signal(action="stop_short", price=state.stop_price))
                return signals
            # Check exit on close
            if close > max_exit:
                signals.append(Signal(action="exit_short", price=close))
                return signals

        # Entry signals (only when flat)
        if state.side == "flat":
            if habilitar_long and close > max_prev:
                stop_long = min_prev * (1.0 - stop_pct)
                signals.append(Signal(action="entry_long", price=close, stop_price=stop_long))

            elif habilitar_short and close < min_prev:
                stop_short = max_prev * (1.0 + stop_pct)
                signals.append(Signal(action="entry_short", price=close, stop_price=stop_short))

        return signals
