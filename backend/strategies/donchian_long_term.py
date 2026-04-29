"""Donchian breakout long-term: tuned for weekly/monthly horizons.

Targets the cells where `donchian_adx_atr` underperforms (1w, 1M). The
existing `support_resistance_trailing` wins those cells by riding multi-year
BTC/ETH bull markets via zigzag pivots. This strategy attacks the same
problem with a different mechanic:

- **No ADX filter.** ADX is too restrictive on weekly/monthly data — only
  the strongest already-mature trends pass, so we miss the early entry that
  matters most over multi-year holds.
- **Higher-TF SMA regime filter** (optional, default disabled). When enabled,
  longs only trigger when close > sma(N), shorts when close < sma(N). Filters
  out catching falling knives in bear cycles.
- **Rolling-min/max trailing stop** (Chandelier-style without ATR). Stop is
  pulled up to the lowest low of the last `trail_lookback` closed candles
  (with optional ATR buffer). Reacts to lower lows faster than an ATR-mult
  trail, locking in gains during regime breaks.

Defaults are sensible for 1w/1M but the param surface allows it to be applied
to any TF — sweep separately per use case.
"""

from __future__ import annotations

import pandas as pd

from backend.strategies.base import ParameterDef, PositionState, Signal, Strategy
from backend.strategies.donchian_adx_atr import _compute_atr


class DonchianLongTermStrategy(Strategy):
    name = "donchian_long_term"
    description = (
        "Recomendada para TIMEFRAMES ALTOS (1w, 1M). Donchian breakout sin "
        "filtro ADX, con filtro de tendencia opcional por SMA y trailing basado "
        "en rolling-min/max (Chandelier sin ATR) que reacciona más rápido a "
        "rupturas de régimen que el trailing por ATR. Pensada para montar "
        "ciclos alcistas multianuales y soltar rápido en bear markets. "
        "Complementaria a donchian_adx_atr, que cubre intradía."
    )

    def get_parameters(self) -> list[ParameterDef]:
        return [
            ParameterDef("donchian_n", "int", 5, 3, 100, "Lookback for entry Donchian channel (prev N high/low)"),
            ParameterDef("donchian_exit_n", "int", 3, 1, 50, "Lookback for opposite-direction exit Donchian channel"),
            ParameterDef(
                "sma_filter_n",
                "int",
                0,
                0,
                200,
                "Higher-TF SMA trend filter length. 0 disables. When >0, longs require close > SMA, shorts close < SMA",
            ),
            ParameterDef(
                "trail_lookback",
                "int",
                10,
                1,
                50,
                "Rolling Min/Max window (in closed candles) for the trailing stop reference",
            ),
            ParameterDef("atr_period", "int", 14, 5, 50, "ATR smoothing period (used as buffer below trailing low)"),
            ParameterDef(
                "atr_buffer_mult",
                "float",
                0.0,
                0.0,
                5.0,
                "ATR buffer multiplier subtracted/added below/above the rolling extreme used as stop. 0 = stop exactly at the extreme.",
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
                "Exit on opposite Donchian breakout. When False, only stop/trail closes the trade.",
            ),
            ParameterDef("coste_total_bps", "float", 10.0, 0.0, 100.0, "Round-trip transaction cost in basis points"),
        ]

    def init(self, params: dict, candles: pd.DataFrame) -> None:
        self.params = params
        n = int(params.get("donchian_n", 10))
        m = int(params.get("donchian_exit_n", 5))
        sma_n = int(params.get("sma_filter_n", 0))
        trail_n = int(params.get("trail_lookback", 5))
        atr_p = int(params.get("atr_period", 14))

        high = candles["high"]
        low = candles["low"]
        close = candles["close"]

        # All indicators .shift(1) so at time t we use values up to t-1 (no lookahead).
        self.max_prev = high.shift(1).rolling(n).max()
        self.min_prev = low.shift(1).rolling(n).min()
        self.max_exit = high.shift(1).rolling(m).max()
        self.min_exit = low.shift(1).rolling(m).min()
        self.trail_min = low.shift(1).rolling(trail_n).min()
        self.trail_max = high.shift(1).rolling(trail_n).max()
        self.atr_prev = _compute_atr(high, low, close, atr_p).shift(1)
        if sma_n > 0:
            self.sma_prev = close.shift(1).rolling(sma_n).mean()
        else:
            self.sma_prev = None

        self.candles = candles

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        params = self.params
        habilitar_long = bool(params.get("habilitar_long", True))
        habilitar_short = bool(params.get("habilitar_short", True))
        salida_por_ruptura = bool(params.get("salida_por_ruptura", True))
        atr_buffer_mult = float(params.get("atr_buffer_mult", 1.0))

        signals: list[Signal] = []

        close = float(candle["close"])
        low = float(candle["low"])
        high = float(candle["high"])

        max_prev = self.max_prev.iloc[t]
        min_prev = self.min_prev.iloc[t]
        max_exit = self.max_exit.iloc[t]
        min_exit = self.min_exit.iloc[t]
        trail_min = self.trail_min.iloc[t]
        trail_max = self.trail_max.iloc[t]
        atr = self.atr_prev.iloc[t]

        if pd.isna(max_prev) or pd.isna(min_prev) or pd.isna(max_exit) or pd.isna(min_exit):
            return signals
        if pd.isna(trail_min) or pd.isna(trail_max) or pd.isna(atr):
            return signals

        # 1. Position management first (stop / exit / trailing).
        if state.side == "long":
            if low <= state.stop_price:
                signals.append(Signal(action="stop_long", price=state.stop_price))
                return signals
            if salida_por_ruptura and close < min_exit:
                signals.append(Signal(action="exit_long", price=close))
                return signals
            candidate = trail_min - atr_buffer_mult * atr
            if candidate > state.stop_price:
                signals.append(Signal(action="move_stop", stop_price=candidate))
            return signals

        if state.side == "short":
            if high >= state.stop_price:
                signals.append(Signal(action="stop_short", price=state.stop_price))
                return signals
            if salida_por_ruptura and close > max_exit:
                signals.append(Signal(action="exit_short", price=close))
                return signals
            candidate = trail_max + atr_buffer_mult * atr
            if candidate < state.stop_price:
                signals.append(Signal(action="move_stop", stop_price=candidate))
            return signals

        # 2. Entry — only when flat. Optional HTF SMA regime filter.
        if self.sma_prev is not None:
            sma = self.sma_prev.iloc[t]
            if pd.isna(sma):
                return signals
            long_regime_ok = close > sma
            short_regime_ok = close < sma
        else:
            long_regime_ok = True
            short_regime_ok = True

        if habilitar_long and long_regime_ok and close > max_prev:
            stop_long = trail_min - atr_buffer_mult * atr
            signals.append(Signal(action="entry_long", price=close, stop_price=stop_long))
        elif habilitar_short and short_regime_ok and close < min_prev:
            stop_short = trail_max + atr_buffer_mult * atr
            signals.append(Signal(action="entry_short", price=close, stop_price=stop_short))

        return signals
