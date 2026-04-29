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
            ParameterDef(
                "N_entrada", "int", 20, 2, 500, "Lookback window for breakout detection (exclusive of current candle)"
            ),
            ParameterDef("M_salida", "int", 10, 1, 500, "Lookback window for exit signal"),
            ParameterDef("stop_pct", "float", 0.02, 0.001, 0.5, "Stop loss percentage from entry reference level"),
            ParameterDef(
                "modo_ejecucion", "str", "open_next", None, None, "Execution mode: 'open_next' or 'close_current'"
            ),
            ParameterDef("habilitar_long", "bool", True, None, None, "Enable long entries"),
            ParameterDef("habilitar_short", "bool", True, None, None, "Enable short entries"),
            ParameterDef(
                "salida_por_ruptura",
                "bool",
                True,
                None,
                None,
                "Exit on reversal breakout (close vs M-candle extreme). When False, only stop-loss closes the trade.",
            ),
            ParameterDef(
                "exit_confirmation_candles",
                "int",
                1,
                1,
                10,
                "Consecutive closed candles below the M-extreme required to confirm exit. 1 = current behaviour (single-candle exit). >1 reduces whipsaws but lags exits.",
            ),
            ParameterDef("coste_total_bps", "float", 10.0, 0.0, 100.0, "Round-trip transaction cost in basis points"),
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

    def _exit_confirmed(self, t: int, side: str, n_confirm: int) -> bool:
        """Return True when the last `n_confirm` consecutive closed candles
        all sit on the wrong side of their respective M-exit channel.

        n_confirm=1 collapses to the original single-candle check.
        """
        if n_confirm <= 1:
            # Caller will do the single-candle check directly; this is just a
            # convenience for the multi-candle path.
            return False
        if t < n_confirm - 1:
            return False
        for k in range(n_confirm):
            idx = t - k
            c_close = float(self.candles.iloc[idx]["close"])
            if side == "long":
                ref = self.min_exit.iloc[idx]
                if pd.isna(ref) or c_close >= float(ref):
                    return False
            else:  # short
                ref = self.max_exit.iloc[idx]
                if pd.isna(ref) or c_close <= float(ref):
                    return False
        return True

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        params = self.params
        habilitar_long = bool(params.get("habilitar_long", True))
        habilitar_short = bool(params.get("habilitar_short", True))
        stop_pct = float(params.get("stop_pct", 0.02))
        salida_por_ruptura = bool(params.get("salida_por_ruptura", True))
        n_confirm = max(1, int(params.get("exit_confirmation_candles", 1)))

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
            # Check exit on close (single or multi-candle confirmation)
            if salida_por_ruptura:
                if n_confirm == 1:
                    if close < min_exit:
                        signals.append(Signal(action="exit_long", price=close))
                        return signals
                elif self._exit_confirmed(t, "long", n_confirm):
                    signals.append(Signal(action="exit_long", price=close))
                    return signals

        elif state.side == "short":
            # Check stop loss (intrabar: triggered on High)
            if high >= state.stop_price:
                signals.append(Signal(action="stop_short", price=state.stop_price))
                return signals
            # Check exit on close (single or multi-candle confirmation)
            if salida_por_ruptura:
                if n_confirm == 1:
                    if close > max_exit:
                        signals.append(Signal(action="exit_short", price=close))
                        return signals
                elif self._exit_confirmed(t, "short", n_confirm):
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
