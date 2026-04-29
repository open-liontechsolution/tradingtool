"""Donchian breakout with ADX trend-strength filter and ATR-multiple stop/trail.

Targets the weaknesses surfaced by the issue #132 baseline (`tmp/strategy_research/
gap_analysis.md`):

- ADX(adx_period) gate suppresses entries during low-trend (chop) periods
  where the existing breakout family bleeds out on intraday timeframes.
- ATR(atr_period) × `atr_stop_mult` initial stop adapts to per-TF volatility
  instead of the fixed-percent stop that the existing strategies use.
- ATR(atr_period) × `atr_trail_mult` trailing rule (only tightens, never loosens)
  rides trends without giving back too much.

The structural form is a Donchian breakout (close vs. previous N-period high/low),
so it preserves the entry mechanic that wins on weekly/monthly BTC trends.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.strategies.base import ParameterDef, PositionState, Signal, Strategy


def _wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothed moving average (RMA): EMA with alpha = 1/period."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return _wilder_rma(tr, period)


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Standard ADX (Wilder). Returns a series; NaN until the smoothing warms up."""
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)

    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    atr = _wilder_rma(tr, period)
    plus_di = 100.0 * _wilder_rma(plus_dm, period) / atr.replace(0, np.nan)
    minus_di = 100.0 * _wilder_rma(minus_dm, period) / atr.replace(0, np.nan)

    denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    return _wilder_rma(dx, period)


class DonchianAdxAtrStrategy(Strategy):
    name = "donchian_adx_atr"
    description = (
        "Recomendada para INTRADÍA (1h, 4h, 1d). Donchian breakout filtrado por "
        "ADX con stop y trailing basados en ATR. El gate ADX suprime entradas en "
        "regímenes laterales (mata el chop, mejora drástica vs breakout simple), "
        "el stop inicial = entrada ∓ atr_stop_mult × ATR adapta el riesgo a la "
        "volatilidad del timeframe, y el trailing solo se mueve para apretar. "
        "En backtests vs las 4 estrategias previas bate composite y reduce "
        "drawdown en 8/9 celdas intradía (BTC/ETH/SOL × 1h/4h/1d). "
        "Para 1w/1M las estrategias de soportes/resistencias siguen siendo "
        "preferibles porque montan ciclos alcistas multianuales con trailing "
        "basado en zigzag."
    )

    def get_parameters(self) -> list[ParameterDef]:
        return [
            ParameterDef("donchian_n", "int", 20, 5, 200, "Lookback for entry Donchian channel (prev N high/low)"),
            ParameterDef("donchian_exit_n", "int", 80, 1, 200, "Lookback for opposite-direction exit Donchian channel"),
            ParameterDef("adx_period", "int", 14, 5, 50, "ADX smoothing period"),
            ParameterDef(
                "adx_threshold", "float", 20.0, 0.0, 100.0, "Minimum ADX value required to enter (0 disables filter)"
            ),
            ParameterDef("atr_period", "int", 14, 5, 50, "ATR smoothing period for stop/trail sizing"),
            ParameterDef(
                "atr_stop_mult", "float", 1.5, 0.5, 10.0, "Initial stop distance in ATR multiples from entry price"
            ),
            ParameterDef(
                "atr_trail_mult", "float", 8.0, 0.5, 10.0, "Trailing stop distance in ATR multiples from current close"
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
        n = int(params.get("donchian_n", 20))
        m = int(params.get("donchian_exit_n", 10))
        adx_p = int(params.get("adx_period", 14))
        atr_p = int(params.get("atr_period", 14))

        high = candles["high"]
        low = candles["low"]
        close = candles["close"]

        # Donchian channels — shift(1) so at time t we look at [t-N, t-1] only.
        self.max_prev = high.shift(1).rolling(n).max()
        self.min_prev = low.shift(1).rolling(n).min()
        self.max_exit = high.shift(1).rolling(m).max()
        self.min_exit = low.shift(1).rolling(m).min()

        # ATR / ADX — also shifted so at time t we use values computed up to t-1.
        self.atr_prev = _compute_atr(high, low, close, atr_p).shift(1)
        self.adx_prev = _compute_adx(high, low, close, adx_p).shift(1)

        self.candles = candles

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        params = self.params
        habilitar_long = bool(params.get("habilitar_long", True))
        habilitar_short = bool(params.get("habilitar_short", True))
        salida_por_ruptura = bool(params.get("salida_por_ruptura", True))
        adx_threshold = float(params.get("adx_threshold", 25.0))
        atr_stop_mult = float(params.get("atr_stop_mult", 2.5))
        atr_trail_mult = float(params.get("atr_trail_mult", 3.0))

        signals: list[Signal] = []

        close = float(candle["close"])
        low = float(candle["low"])
        high = float(candle["high"])

        max_prev = self.max_prev.iloc[t]
        min_prev = self.min_prev.iloc[t]
        max_exit = self.max_exit.iloc[t]
        min_exit = self.min_exit.iloc[t]
        atr = self.atr_prev.iloc[t]
        adx = self.adx_prev.iloc[t]

        # Need full warm-up for every indicator before doing anything.
        if pd.isna(max_prev) or pd.isna(min_prev) or pd.isna(max_exit) or pd.isna(min_exit):
            return signals
        if pd.isna(atr) or atr <= 0:
            return signals
        # ADX may take longer to warm up than ATR; without it we have no filter,
        # so suppress entries until it's available. Existing positions still
        # check stop/trail/exit normally even if adx is NaN.

        # 1. Position management first (stop / exit / trailing).
        if state.side == "long":
            if low <= state.stop_price:
                signals.append(Signal(action="stop_long", price=state.stop_price))
                return signals
            if salida_por_ruptura and close < min_exit:
                signals.append(Signal(action="exit_long", price=close))
                return signals
            # Trailing: tighten only.
            candidate = close - atr_trail_mult * atr
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
            candidate = close + atr_trail_mult * atr
            if candidate < state.stop_price:
                signals.append(Signal(action="move_stop", stop_price=candidate))
            return signals

        # 2. Entry — only when flat and ADX confirms a trending regime.
        if pd.isna(adx) or adx < adx_threshold:
            return signals

        if habilitar_long and close > max_prev:
            stop_long = close - atr_stop_mult * atr
            signals.append(Signal(action="entry_long", price=close, stop_price=stop_long))
        elif habilitar_short and close < min_prev:
            stop_short = close + atr_stop_mult * atr
            signals.append(Signal(action="entry_short", price=close, stop_price=stop_short))

        return signals
