"""Zigzag pivots with momentum (RSI) confirmation and ATR-padded trailing.

Reuses the zigzag detection of `support_resistance` for entry levels but
adds two layers:
- **RSI momentum filter at entry**: longs require RSI > `rsi_long_threshold`,
  shorts require RSI < `rsi_short_threshold`. Filters out weak breakouts
  where momentum doesn't confirm the direction.
- **ATR-padded trailing**: trail to `support - atr_buffer × ATR` (long) /
  `resistance + atr_buffer × ATR` (short). Adapts the stop buffer to
  per-TF volatility instead of a fixed percent.

Hypothesis: `support_resistance_trailing` wins on weekly trends but takes
some false breakouts. Filtering with RSI should lift profit factor; ATR
buffer should give better DD on volatile cells.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.strategies.base import ParameterDef, PositionState, Signal, Strategy
from backend.strategies.donchian_adx_atr import _compute_atr
from backend.strategies.support_resistance import SupportResistanceStrategy


def _compute_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


class ZigzagMomentumStrategy(Strategy):
    name = "zigzag_momentum"
    description = (
        "Zigzag (soportes/resistencias) con confirmación RSI al entrar y "
        "trailing del stop al nivel zigzag con buffer ATR. Mecanismo de "
        "S/R-trailing pero filtrando entradas débiles y adaptando el buffer "
        "del stop a la volatilidad del timeframe."
    )

    def get_parameters(self) -> list[ParameterDef]:
        return [
            ParameterDef(
                "reversal_pct", "float", 0.03, 0.005, 0.5, "Minimum % reversal from extreme to confirm a zigzag swing"
            ),
            ParameterDef("rsi_period", "int", 14, 2, 50, "RSI smoothing period"),
            ParameterDef(
                "rsi_long_threshold",
                "float",
                30.0,
                0.0,
                100.0,
                "Min RSI value at entry for longs (50 = neutral; higher = more selective)",
            ),
            ParameterDef(
                "rsi_short_threshold",
                "float",
                50.0,
                0.0,
                100.0,
                "Max RSI value at entry for shorts (50 = neutral; lower = more selective)",
            ),
            ParameterDef("atr_period", "int", 14, 5, 50, "ATR smoothing period for stop buffer"),
            ParameterDef(
                "atr_buffer_mult",
                "float",
                1.5,
                0.0,
                5.0,
                "ATR buffer below/above zigzag level used as stop. 0 = stop exactly at level",
            ),
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
                "Exit on opposite zigzag breakout. When False, only stop closes the trade.",
            ),
            ParameterDef("coste_total_bps", "float", 10.0, 0.0, 100.0, "Round-trip transaction cost in basis points"),
        ]

    def init(self, params: dict, candles: pd.DataFrame) -> None:
        self.params = params
        reversal_pct = float(params.get("reversal_pct", 0.03))
        rsi_period = int(params.get("rsi_period", 14))
        atr_period = int(params.get("atr_period", 14))

        highs = candles["high"].to_numpy(dtype=float)
        lows = candles["low"].to_numpy(dtype=float)
        # Reuse zigzag from SupportResistanceStrategy
        support, resistance = SupportResistanceStrategy._compute_zigzag(highs, lows, reversal_pct)
        self.last_support = support
        self.last_resistance = resistance

        # RSI / ATR shifted so at time t we look at values up to t-1
        self.rsi_prev = _compute_rsi(candles["close"], rsi_period).shift(1)
        self.atr_prev = _compute_atr(candles["high"], candles["low"], candles["close"], atr_period).shift(1)

        self.candles = candles

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        params = self.params
        habilitar_long = bool(params.get("habilitar_long", True))
        habilitar_short = bool(params.get("habilitar_short", True))
        salida_por_ruptura = bool(params.get("salida_por_ruptura", True))
        rsi_long_threshold = float(params.get("rsi_long_threshold", 50.0))
        rsi_short_threshold = float(params.get("rsi_short_threshold", 50.0))
        atr_buffer_mult = float(params.get("atr_buffer_mult", 1.0))

        signals: list[Signal] = []

        close = float(candle["close"])
        low = float(candle["low"])
        high = float(candle["high"])

        support = self.last_support[t]
        resistance = self.last_resistance[t]
        rsi = self.rsi_prev.iloc[t]
        atr = self.atr_prev.iloc[t]

        if np.isnan(support) or np.isnan(resistance):
            return signals
        if pd.isna(atr) or atr <= 0:
            return signals

        # 1. Position management — check stops/exits/trailing.
        if state.side == "long":
            if low <= state.stop_price:
                signals.append(Signal(action="stop_long", price=state.stop_price))
                return signals
            if salida_por_ruptura and close < support:
                signals.append(Signal(action="exit_long", price=close))
                return signals
            # Trail to the latest support with ATR buffer (only if it tightens).
            candidate = support - atr_buffer_mult * atr
            if candidate > state.stop_price:
                signals.append(Signal(action="move_stop", stop_price=candidate))
            return signals

        if state.side == "short":
            if high >= state.stop_price:
                signals.append(Signal(action="stop_short", price=state.stop_price))
                return signals
            if salida_por_ruptura and close > resistance:
                signals.append(Signal(action="exit_short", price=close))
                return signals
            candidate = resistance + atr_buffer_mult * atr
            if candidate < state.stop_price:
                signals.append(Signal(action="move_stop", stop_price=candidate))
            return signals

        # 2. Entry — only when flat. RSI must confirm direction.
        if pd.isna(rsi):
            return signals
        if habilitar_long and close > resistance and rsi >= rsi_long_threshold:
            stop_long = support - atr_buffer_mult * atr
            signals.append(Signal(action="entry_long", price=close, stop_price=stop_long))
        elif habilitar_short and close < support and rsi <= rsi_short_threshold:
            stop_short = resistance + atr_buffer_mult * atr
            signals.append(Signal(action="entry_short", price=close, stop_price=stop_short))

        return signals
