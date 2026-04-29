"""Mean-reversion strategy using Bollinger Bands + RSI confirmation.

Designed for chop / post-bull regimes where breakouts get whipsawed but
price keeps reverting to the mean. Buys oversold dips and sells overbought
rallies; exits at the moving-average mid-line (or the opposite band).

Entry long
==========
Close pierces the LOWER Bollinger Band (close <= mean - bb_std × std)
AND RSI < ``rsi_oversold`` (default 30) AND (optional) close > SMA(htf)
for higher-TF trend alignment when ``sma_filter_n > 0``.

Entry short (mirror)
====================
Close pierces the UPPER band AND RSI > ``rsi_overbought`` AND (optional)
close < SMA(htf).

Exits
=====
- ``stop_pct`` below entry for longs / above for shorts (initial stop)
- Profit target: close >= mean (long) / close <= mean (short)  [default ON,
  can be disabled via ``salida_a_mean=False``]
- Optional opposite-band exit: ``salida_banda_opuesta`` (default OFF) ⇒
  long exits when close >= upper band, short when close <= lower band.

Notes
=====
- This is a NEW strategy (not an inheritor) so the breakout/SR family
  stays unchanged.
- Mean-reversion is structurally OPPOSITE to breakout: it bets the move
  reverses, not continues. Pair with a regime filter (sma_filter_n) for
  best results on real markets.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.strategies.base import ParameterDef, PositionState, Signal, Strategy


def _compute_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


class MeanReversionBBStrategy(Strategy):
    name = "mean_reversion_bb"
    description = (
        "Mean reversion: compra cuando el cierre toca la banda Bollinger inferior "
        "con RSI sobreventa, vende cuando toca la superior con sobrecompra. Pensada "
        "para mercados laterales/chop donde los breakouts fallan. Salida por defecto "
        "al cruzar la media móvil (target conservador) o por stop %. Opcionalmente "
        "filtra entradas por SMA de timeframe alto."
    )

    def get_parameters(self) -> list[ParameterDef]:
        return [
            ParameterDef("bb_period", "int", 20, 5, 100, "Bollinger Bands moving-average period"),
            ParameterDef("bb_std", "float", 2.0, 0.5, 4.0, "Bollinger Bands standard-deviation multiplier"),
            ParameterDef("rsi_period", "int", 14, 2, 50, "RSI smoothing period"),
            ParameterDef(
                "rsi_oversold",
                "float",
                30.0,
                0.0,
                50.0,
                "RSI threshold below which longs may enter (lower = more selective)",
            ),
            ParameterDef(
                "rsi_overbought",
                "float",
                70.0,
                50.0,
                100.0,
                "RSI threshold above which shorts may enter (higher = more selective)",
            ),
            ParameterDef("stop_pct", "float", 0.03, 0.001, 0.5, "Initial stop loss percentage from entry price"),
            ParameterDef(
                "salida_a_mean",
                "bool",
                True,
                None,
                None,
                "Exit at the Bollinger mean (target). When False, only stop or opposite-band exit closes the trade.",
            ),
            ParameterDef(
                "salida_banda_opuesta",
                "bool",
                False,
                None,
                None,
                "Also exit when price reaches the OPPOSITE band (long: at upper; short: at lower). Slower exit, larger profits when the move continues.",
            ),
            ParameterDef(
                "sma_filter_n",
                "int",
                0,
                0,
                500,
                "Higher-TF SMA trend filter length. 0 disables. When >0, longs require close > SMA(N), shorts close < SMA(N).",
            ),
            ParameterDef(
                "modo_ejecucion", "str", "open_next", None, None, "Execution mode: 'open_next' or 'close_current'"
            ),
            ParameterDef("habilitar_long", "bool", True, None, None, "Enable long entries"),
            ParameterDef("habilitar_short", "bool", True, None, None, "Enable short entries"),
            ParameterDef("coste_total_bps", "float", 10.0, 0.0, 100.0, "Round-trip transaction cost in basis points"),
        ]

    def init(self, params: dict, candles: pd.DataFrame) -> None:
        self.params = params
        bb_n = int(params.get("bb_period", 20))
        bb_k = float(params.get("bb_std", 2.0))
        rsi_n = int(params.get("rsi_period", 14))
        sma_n = int(params.get("sma_filter_n", 0))

        close = candles["close"]

        # Bollinger Bands — shifted so at time t we use values up to t-1.
        ma = close.rolling(bb_n).mean()
        std = close.rolling(bb_n).std()
        self.bb_mean_prev = ma.shift(1)
        self.bb_upper_prev = (ma + bb_k * std).shift(1)
        self.bb_lower_prev = (ma - bb_k * std).shift(1)

        self.rsi_prev = _compute_rsi(close, rsi_n).shift(1)

        self.sma_prev = close.shift(1).rolling(sma_n).mean() if sma_n > 0 else None

        self.candles = candles

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        params = self.params
        habilitar_long = bool(params.get("habilitar_long", True))
        habilitar_short = bool(params.get("habilitar_short", True))
        rsi_oversold = float(params.get("rsi_oversold", 30.0))
        rsi_overbought = float(params.get("rsi_overbought", 70.0))
        stop_pct = float(params.get("stop_pct", 0.03))
        salida_a_mean = bool(params.get("salida_a_mean", True))
        salida_banda_opuesta = bool(params.get("salida_banda_opuesta", False))

        signals: list[Signal] = []

        close = float(candle["close"])
        low = float(candle["low"])
        high = float(candle["high"])

        bb_mean = self.bb_mean_prev.iloc[t]
        bb_upper = self.bb_upper_prev.iloc[t]
        bb_lower = self.bb_lower_prev.iloc[t]
        rsi = self.rsi_prev.iloc[t]

        if pd.isna(bb_mean) or pd.isna(bb_upper) or pd.isna(bb_lower):
            return signals

        # Position management first
        if state.side == "long":
            if low <= state.stop_price:
                signals.append(Signal(action="stop_long", price=state.stop_price))
                return signals
            if salida_a_mean and close >= bb_mean:
                signals.append(Signal(action="exit_long", price=close))
                return signals
            if salida_banda_opuesta and close >= bb_upper:
                signals.append(Signal(action="exit_long", price=close))
                return signals
            return signals

        if state.side == "short":
            if high >= state.stop_price:
                signals.append(Signal(action="stop_short", price=state.stop_price))
                return signals
            if salida_a_mean and close <= bb_mean:
                signals.append(Signal(action="exit_short", price=close))
                return signals
            if salida_banda_opuesta and close <= bb_lower:
                signals.append(Signal(action="exit_short", price=close))
                return signals
            return signals

        # Entry — only when flat. Need RSI valid for the confirmation gate.
        if pd.isna(rsi):
            return signals

        # Optional HTF SMA trend filter
        long_regime_ok = True
        short_regime_ok = True
        if self.sma_prev is not None:
            sma = self.sma_prev.iloc[t]
            if pd.isna(sma):
                return signals
            long_regime_ok = close > float(sma)
            short_regime_ok = close < float(sma)

        if habilitar_long and long_regime_ok and close <= bb_lower and rsi <= rsi_oversold:
            stop_long = close * (1.0 - stop_pct)
            signals.append(Signal(action="entry_long", price=close, stop_price=stop_long))
        elif habilitar_short and short_regime_ok and close >= bb_upper and rsi >= rsi_overbought:
            stop_short = close * (1.0 + stop_pct)
            signals.append(Signal(action="entry_short", price=close, stop_price=stop_short))

        return signals
