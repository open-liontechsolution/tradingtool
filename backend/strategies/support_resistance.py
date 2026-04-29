"""Support/Resistance strategy: zigzag-based swing detection with breakout entries."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.strategies.base import ParameterDef, PositionState, Signal, Strategy


class SupportResistanceStrategy(Strategy):
    name = "support_resistance"
    description = (
        "Soportes y Resistencias reales mediante zigzag. "
        "Detecta swing highs (resistencias) y swing lows (soportes) cuando el precio "
        "retrocede un porcentaje mínimo desde el extremo. "
        "Entry long al romper resistencia, entry short al romper soporte. "
        "Exit cuando se rompe el nivel contrario. Stop porcentual sobre soporte/resistencia."
    )

    def get_parameters(self) -> list[ParameterDef]:
        return [
            ParameterDef(
                "reversal_pct",
                "float",
                0.03,
                0.005,
                0.5,
                "Minimum % reversal from extreme to confirm a swing point (e.g. 0.03 = 3%)",
            ),
            ParameterDef("stop_pct", "float", 0.02, 0.001, 0.5, "Stop loss percentage from support/resistance level"),
            ParameterDef(
                "modo_ejecucion", "str", "open_next", None, None, "Execution mode: 'open_next' or 'close_current'"
            ),
            ParameterDef("habilitar_long", "bool", True, None, None, "Enable long entries"),
            ParameterDef("habilitar_short", "bool", True, None, None, "Enable short entries"),
            ParameterDef(
                "exit_confirmation_candles",
                "int",
                1,
                1,
                10,
                "Consecutive closed candles below the support (long) / above the resistance (short) required to confirm the exit. 1 = original single-candle exit; >1 reduces whipsaws but lags exits.",
            ),
            ParameterDef(
                "sma_filter_n",
                "int",
                0,
                0,
                500,
                "Higher-TF SMA trend filter length. 0 disables (legacy). When >0, longs require close > SMA(N), shorts close < SMA(N). Stops major counter-trend trades in regime markets.",
            ),
            ParameterDef("coste_total_bps", "float", 10.0, 0.0, 100.0, "Round-trip transaction cost in basis points"),
        ]

    # ------------------------------------------------------------------
    # Zigzag computation (no lookahead)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_zigzag(highs: np.ndarray, lows: np.ndarray, reversal_pct: float):
        """Pre-compute last confirmed support and resistance for every bar.

        Returns two arrays of length n:
          last_support[t]    – last confirmed swing low  as of bar t (NaN while unknown)
          last_resistance[t] – last confirmed swing high as of bar t (NaN while unknown)
        """
        n = len(highs)
        last_support = np.full(n, np.nan)
        last_resistance = np.full(n, np.nan)

        if n == 0:
            return last_support, last_resistance

        # Bootstrap: start by tracking in both directions from first candle
        # direction: 'up' means we are tracking towards a potential swing high
        #            'down' means we are tracking towards a potential swing low
        direction = "up"
        current_high = highs[0]
        current_low = lows[0]

        confirmed_support = np.nan
        confirmed_resistance = np.nan

        for t in range(n):
            h = highs[t]
            low_t = lows[t]

            if direction == "up":
                # Looking for a swing high
                if h > current_high:
                    current_high = h
                # Check if price has reversed enough from the running high
                if current_high > 0 and low_t <= current_high * (1.0 - reversal_pct):
                    # Confirm the swing high as resistance
                    confirmed_resistance = current_high
                    # Now switch to looking for a swing low
                    direction = "down"
                    current_low = low_t
            else:
                # Looking for a swing low
                if low_t < current_low:
                    current_low = low_t
                # Check if price has reversed enough from the running low
                if current_low > 0 and h >= current_low * (1.0 + reversal_pct):
                    # Confirm the swing low as support
                    confirmed_support = current_low
                    # Now switch to looking for a swing high
                    direction = "up"
                    current_high = h

            last_support[t] = confirmed_support
            last_resistance[t] = confirmed_resistance

        return last_support, last_resistance

    # ------------------------------------------------------------------
    # Strategy interface
    # ------------------------------------------------------------------

    def init(self, params: dict, candles: pd.DataFrame) -> None:
        self.params = params
        reversal_pct = float(params.get("reversal_pct", 0.03))
        sma_n = int(params.get("sma_filter_n", 0))

        highs = candles["high"].to_numpy(dtype=float)
        lows = candles["low"].to_numpy(dtype=float)

        support, resistance = self._compute_zigzag(highs, lows, reversal_pct)
        self.last_support = support
        self.last_resistance = resistance

        # Optional HTF SMA trend filter (shifted so we use values up to t-1)
        self.sma_prev = candles["close"].shift(1).rolling(sma_n).mean() if sma_n > 0 else None

        self.candles = candles

    def _exit_confirmed_sr(self, t: int, side: str, n_confirm: int) -> bool:
        """Multi-candle confirmation: last N closed candles all on the wrong
        side of their respective zigzag level. n_confirm <= 1 ⇒ False (caller
        handles single-candle path)."""
        if n_confirm <= 1 or t < n_confirm - 1:
            return False
        for k in range(n_confirm):
            idx = t - k
            c_close = float(self.candles.iloc[idx]["close"])
            if side == "long":
                ref = self.last_support[idx]
                if np.isnan(ref) or c_close >= float(ref):
                    return False
            else:  # short
                ref = self.last_resistance[idx]
                if np.isnan(ref) or c_close <= float(ref):
                    return False
        return True

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        params = self.params
        habilitar_long = bool(params.get("habilitar_long", True))
        habilitar_short = bool(params.get("habilitar_short", True))
        stop_pct = float(params.get("stop_pct", 0.02))
        n_confirm = max(1, int(params.get("exit_confirmation_candles", 1)))

        signals: list[Signal] = []

        close = float(candle["close"])
        low = float(candle["low"])
        high = float(candle["high"])

        support = self.last_support[t]
        resistance = self.last_resistance[t]

        # Need both levels confirmed before generating signals
        if np.isnan(support) or np.isnan(resistance):
            return signals

        if state.side == "long":
            # Check stop loss (intrabar: triggered on Low)
            if low <= state.stop_price:
                signals.append(Signal(action="stop_long", price=state.stop_price))
                return signals
            # Check exit: close breaks below support (single or multi-candle)
            if n_confirm == 1:
                if close < support:
                    signals.append(Signal(action="exit_long", price=close))
                    return signals
            elif self._exit_confirmed_sr(t, "long", n_confirm):
                signals.append(Signal(action="exit_long", price=close))
                return signals

        elif state.side == "short":
            # Check stop loss (intrabar: triggered on High)
            if high >= state.stop_price:
                signals.append(Signal(action="stop_short", price=state.stop_price))
                return signals
            # Check exit: close breaks above resistance (single or multi-candle)
            if n_confirm == 1:
                if close > resistance:
                    signals.append(Signal(action="exit_short", price=close))
                    return signals
            elif self._exit_confirmed_sr(t, "short", n_confirm):
                signals.append(Signal(action="exit_short", price=close))
                return signals

        # Entry signals (only when flat)
        if state.side == "flat":
            # Optional HTF SMA trend filter
            long_regime_ok = True
            short_regime_ok = True
            if self.sma_prev is not None:
                sma = self.sma_prev.iloc[t]
                if pd.isna(sma):
                    return signals  # warm-up not done yet
                long_regime_ok = close > float(sma)
                short_regime_ok = close < float(sma)

            if habilitar_long and long_regime_ok and close > resistance:
                stop_long = support * (1.0 - stop_pct)
                signals.append(Signal(action="entry_long", price=close, stop_price=stop_long))

            elif habilitar_short and short_regime_ok and close < support:
                stop_short = resistance * (1.0 + stop_pct)
                signals.append(Signal(action="entry_short", price=close, stop_price=stop_short))

        return signals
