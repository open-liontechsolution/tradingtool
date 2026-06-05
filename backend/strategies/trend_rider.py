"""Leveraged momentum trend-rider — asymmetric payoff for big bull-year gains.

Purpose: a profile built to MAXIMISE the winners and ATTENUATE the losers, so
that under moderate leverage a strong bull year can compound to a large multiple
(x10 target) while down/bear years stay roughly flat (no leveraged bleed).

This is the structural OPPOSITE of ``rsi_reversion`` (which is high win-rate /
small capped wins). Here the win-rate is LOW (~30-45%) but the payoff is large:
a few trends carry the year. Long-only by default — leveraged shorts in crypto
are an extra ruin vector and the bull legs are the prize.

Entry (long)
============
ALL must hold:
  - Donchian breakout: ``close`` makes a new ``donchian_n``-candle high (momentum).
  - Regime gate (the loss-attenuator): ``close > SMA(regime_sma_n)`` AND, when
    ``regime_slope_n > 0``, the SMA is RISING (``SMA_now > SMA(regime_slope_n
    candles ago)``). This keeps us OUT of bear / distribution regimes where a
    leveraged long gets chopped to pieces.
  - ADX(adx_period) >= ``adx_threshold`` (trend-strength gate; 0 disables).

Exits (priority order)
======================
  1. Initial / trailing stop: ``entry - atr_stop_mult*ATR`` at entry, then a
     chandelier trail ``close - atr_trail_mult*ATR`` that only ever tightens
     (emitted as ``move_stop`` — wired into both engines). Riding the trail is
     what lets a winner run for the whole bull leg.
  2. Regime-flip exit (``exit_on_regime_flip``): close < SMA(regime_sma_n) →
     leave before the slow trend-death gives back the leveraged gains.
  3. Optional opposite-Donchian exit (``salida_por_ruptura``).

Look-ahead
==========
Same discipline as the rest of the trend family (``donchian_adx_atr``): all
indicators are ``shift(1)`` (values known up to t-1) and combined with candle
t's own close/high/low. Under ``open_next`` the fill is at t+1's open.
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
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return _wilder_rma(tr, period)


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
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


class TrendRiderStrategy(Strategy):
    name = "trend_rider"
    description = (
        "Trend-following apalancable de payoff asimétrico: maximiza las ganadoras "
        "(breakout Donchian + trailing ATR que deja correr la tendencia) y atenúa "
        "las perdedoras (filtro de régimen: solo largos con SMA al alza, y salida "
        "al perder la SMA de régimen). Long-only por defecto. Pensada para que, con "
        "apalancamiento moderado (2-3x), un año claramente alcista componga un "
        "múltiplo grande mientras los años bajistas se quedan casi planos (sin "
        "sangrado apalancado). Acierto bajo, pero pocas ganadoras enormes cargan el año."
    )

    def get_parameters(self) -> list[ParameterDef]:
        return [
            ParameterDef("donchian_n", "int", 20, 5, 200, "Entry Donchian lookback (new N-candle high)."),
            ParameterDef(
                "donchian_exit_n", "int", 10, 1, 200, "Opposite-Donchian exit lookback (when salida_por_ruptura)."
            ),
            ParameterDef("regime_sma_n", "int", 100, 0, 400, "Regime SMA length. 0 disables the regime gate."),
            ParameterDef(
                "regime_slope_n", "int", 40, 0, 200, "SMA must be rising vs N candles ago. 0 = only require price>SMA."
            ),
            ParameterDef("adx_period", "int", 14, 5, 50, "ADX smoothing period."),
            ParameterDef("adx_threshold", "float", 15.0, 0.0, 100.0, "Minimum ADX to enter (0 disables)."),
            ParameterDef("atr_period", "int", 14, 5, 50, "ATR period for stop/trail sizing."),
            ParameterDef("atr_stop_mult", "float", 3.0, 0.5, 10.0, "Initial stop distance in ATR multiples."),
            ParameterDef(
                "atr_trail_mult", "float", 8.0, 0.5, 15.0, "Trailing stop distance in ATR multiples (rides trends)."
            ),
            ParameterDef("exit_on_regime_flip", "bool", True, None, None, "Exit long when close < SMA(regime_sma_n)."),
            ParameterDef("salida_por_ruptura", "bool", False, None, None, "Also exit on opposite Donchian breakout."),
            ParameterDef(
                "modo_ejecucion", "str", "open_next", None, None, "Execution mode: 'open_next' or 'close_current'."
            ),
            ParameterDef("habilitar_long", "bool", True, None, None, "Enable long entries."),
            ParameterDef(
                "habilitar_short", "bool", False, None, None, "Enable short entries (off — leverage ruin risk)."
            ),
            ParameterDef("coste_total_bps", "float", 10.0, 0.0, 100.0, "Round-trip transaction cost in basis points."),
        ]

    def init(self, params: dict, candles: pd.DataFrame) -> None:
        self.params = params
        n = int(params.get("donchian_n", 20))
        m = int(params.get("donchian_exit_n", 10))
        sma_n = int(params.get("regime_sma_n", 100))
        slope_n = int(params.get("regime_slope_n", 40))
        adx_p = int(params.get("adx_period", 14))
        atr_p = int(params.get("atr_period", 14))

        high = candles["high"]
        low = candles["low"]
        close = candles["close"]

        # Donchian channels — shift(1): at t we use [t-N, t-1].
        self.max_prev = high.shift(1).rolling(n).max()
        self.min_prev = low.shift(1).rolling(n).min()
        self.max_exit = high.shift(1).rolling(m).max()
        self.min_exit = low.shift(1).rolling(m).min()

        # Regime SMA + slope reference, shifted so at t we use values up to t-1.
        sma = close.rolling(sma_n).mean() if sma_n > 0 else None
        self.sma_prev = sma.shift(1) if sma is not None else None
        self.sma_old = sma.shift(1 + slope_n) if (sma is not None and slope_n > 0) else None

        self.atr_prev = _compute_atr(high, low, close, atr_p).shift(1)
        self.adx_prev = _compute_adx(high, low, close, adx_p).shift(1)

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        params = self.params
        habilitar_long = bool(params.get("habilitar_long", True))
        habilitar_short = bool(params.get("habilitar_short", False))
        salida_por_ruptura = bool(params.get("salida_por_ruptura", False))
        exit_on_regime_flip = bool(params.get("exit_on_regime_flip", True))
        adx_threshold = float(params.get("adx_threshold", 15.0))
        atr_stop_mult = float(params.get("atr_stop_mult", 3.0))
        atr_trail_mult = float(params.get("atr_trail_mult", 8.0))

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
        sma = self.sma_prev.iloc[t] if self.sma_prev is not None else None

        if pd.isna(max_prev) or pd.isna(min_prev) or pd.isna(max_exit) or pd.isna(min_exit):
            return signals
        if pd.isna(atr) or atr <= 0:
            return signals

        # 1. Position management first (stop / regime-flip / breakout exit / trail).
        if state.side == "long":
            if low <= state.stop_price:
                signals.append(Signal(action="stop_long", price=state.stop_price))
                return signals
            if exit_on_regime_flip and sma is not None and not pd.isna(sma) and close < float(sma):
                signals.append(Signal(action="exit_long", price=close))
                return signals
            if salida_por_ruptura and close < min_exit:
                signals.append(Signal(action="exit_long", price=close))
                return signals
            candidate = close - atr_trail_mult * atr
            if candidate > state.stop_price:
                signals.append(Signal(action="move_stop", stop_price=candidate))
            return signals

        if state.side == "short":
            if high >= state.stop_price:
                signals.append(Signal(action="stop_short", price=state.stop_price))
                return signals
            if exit_on_regime_flip and sma is not None and not pd.isna(sma) and close > float(sma):
                signals.append(Signal(action="exit_short", price=close))
                return signals
            if salida_por_ruptura and close > max_exit:
                signals.append(Signal(action="exit_short", price=close))
                return signals
            candidate = close + atr_trail_mult * atr
            if candidate < state.stop_price:
                signals.append(Signal(action="move_stop", stop_price=candidate))
            return signals

        # 2. Entry — flat only, ADX confirms trend.
        if pd.isna(adx) or adx < adx_threshold:
            return signals

        # Regime gate (loss-attenuator).
        long_regime = True
        short_regime = True
        if self.sma_prev is not None:
            if sma is None or pd.isna(sma):
                return signals
            rising = True
            falling = True
            if self.sma_old is not None:
                sma_old = self.sma_old.iloc[t]
                if pd.isna(sma_old):
                    return signals
                rising = float(sma) > float(sma_old)
                falling = float(sma) < float(sma_old)
            long_regime = close > float(sma) and rising
            short_regime = close < float(sma) and falling

        if habilitar_long and long_regime and close > max_prev:
            stop_long = close - atr_stop_mult * atr
            signals.append(Signal(action="entry_long", price=close, stop_price=stop_long))
        elif habilitar_short and short_regime and close < min_prev:
            stop_short = close + atr_stop_mult * atr
            signals.append(Signal(action="entry_short", price=close, stop_price=stop_short))

        return signals
