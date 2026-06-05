"""Connors-style RSI mean-reversion strategy (high win-rate by design).

The thesis is the opposite of the breakout/Donchian family: in a market that
is *trending up on the higher timeframe* but pulls back on the lower one, a
short-period RSI dip is usually bought back quickly. So we only buy dips while
price is above a long trend SMA, then take the first modest bounce. Most dips
bounce a little before they break down, which produces many small winners and
only the occasional (rare) stop-out — i.e. a high *win-rate* profile.

Entry long
==========
``close > SMA(trend_sma_n)`` (higher-TF uptrend regime; 0 disables the filter)
AND ``RSI(rsi_period) <= rsi_entry_long``.

Entry short (mirror, OFF by default)
====================================
``close < SMA(trend_sma_n)`` AND ``RSI(rsi_period) >= rsi_entry_short``.

Exit (``exit_mode``)
====================
- ``rsi``       — long exits when RSI rises back to ``rsi_exit_long``; short
                  when RSI falls to ``rsi_exit_short``. (Connors classic.)
- ``sma``       — long exits when ``close >= SMA(exit_sma_n)``; short mirror.
- ``target``    — fixed ``target_pct`` move from entry.
- ``first_up``  — long exits on the first candle that closes above the prior
                  close (mirror for short). Fastest exit, highest win-rate.

Risk
====
- ``stop_pct`` catastrophic stop (intentionally *wide* — it is a backstop, not
  the primary exit; tight stops would convert small winners into losers and
  collapse the win-rate).
- ``max_hold`` optional time stop: force a market exit after N candles so a
  thesis that never reverts does not sit open forever. 0 disables.

Look-ahead
==========
All indicators are computed through candle ``t`` (no shift) and combined with
candle ``t``'s own close at decision time. Under the default ``open_next``
execution this is strictly look-ahead-free — the fill happens at ``t+1``'s
open, strictly after every value used. Stop/target checks use the current
candle's low/high (intrabar), mirroring the rest of the strategy family.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.strategies.base import ParameterDef, PositionState, Signal, Strategy


def _compute_rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI (EWMA smoothing), matching mean_reversion_bb's helper."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


class RsiReversionStrategy(Strategy):
    name = "rsi_reversion"
    description = (
        "Mean reversion estilo Connors RSI-2: compra retrocesos (RSI corto en sobreventa) "
        "solo cuando el precio está por encima de una SMA de tendencia, y vende el primer "
        "rebote. Genera muchas ganancias pequeñas y pocas pérdidas (alto % de acierto). "
        "El stop es deliberadamente amplio (red de seguridad, no salida principal). Long-only "
        "por defecto; los cortos son opcionales para regímenes bajistas."
    )

    def get_parameters(self) -> list[ParameterDef]:
        return [
            ParameterDef("trend_sma_n", "int", 200, 0, 500, "Trend-filter SMA length. 0 disables the regime gate."),
            ParameterDef("rsi_period", "int", 3, 2, 50, "RSI period (short — Connors-style dip detector)."),
            ParameterDef("rsi_entry_long", "float", 20.0, 1.0, 50.0, "Enter long when RSI <= this (oversold dip)."),
            ParameterDef("rsi_entry_short", "float", 80.0, 50.0, 99.0, "Enter short when RSI >= this (overbought)."),
            ParameterDef("exit_mode", "str", "target", None, None, "Exit rule: 'rsi' | 'sma' | 'target' | 'first_up'."),
            ParameterDef("rsi_exit_long", "float", 60.0, 30.0, 95.0, "exit_mode='rsi': long exits when RSI >= this."),
            ParameterDef("rsi_exit_short", "float", 40.0, 5.0, 70.0, "exit_mode='rsi': short exits when RSI <= this."),
            ParameterDef("exit_sma_n", "int", 5, 2, 100, "exit_mode='sma': exit when close crosses SMA(N)."),
            ParameterDef("target_pct", "float", 0.05, 0.002, 0.5, "exit_mode='target': profit target fraction."),
            ParameterDef(
                "stop_pct", "float", 0.15, 0.005, 0.9, "Catastrophic stop fraction from entry (wide backstop)."
            ),
            ParameterDef("max_hold", "int", 0, 0, 1000, "Time stop: force exit after N candles. 0 disables."),
            ParameterDef("habilitar_long", "bool", True, None, None, "Enable long entries."),
            ParameterDef("habilitar_short", "bool", False, None, None, "Enable short entries."),
            ParameterDef(
                "modo_ejecucion", "str", "open_next", None, None, "Execution mode: 'open_next' or 'close_current'."
            ),
            ParameterDef("coste_total_bps", "float", 10.0, 0.0, 100.0, "Round-trip transaction cost in basis points."),
        ]

    def init(self, params: dict, candles: pd.DataFrame) -> None:
        self.params = params
        trend_n = int(params.get("trend_sma_n", 200))
        rsi_n = int(params.get("rsi_period", 3))
        exit_sma_n = int(params.get("exit_sma_n", 5))

        close = candles["close"]

        # Indicators computed THROUGH candle t (no shift). Look-ahead-free under
        # open_next (fill at t+1 open). See module docstring.
        self.rsi = _compute_rsi(close, rsi_n)
        self.trend_sma = close.rolling(trend_n).mean() if trend_n > 0 else None
        self.exit_sma = close.rolling(exit_sma_n).mean()
        self.prev_close = close.shift(1)

        # open_time -> row index, for the max_hold time stop (O(1) lookup vs a
        # per-candle DataFrame scan over tens of thousands of rows).
        self.idx_of: dict[int, int] = {int(ot): i for i, ot in enumerate(candles["open_time"].to_numpy())}

    def on_candle(self, t: int, candle: pd.Series, state: PositionState) -> list[Signal]:
        params = self.params
        signals: list[Signal] = []

        close = float(candle["close"])
        low = float(candle["low"])
        high = float(candle["high"])

        rsi = self.rsi.iloc[t]
        exit_sma = self.exit_sma.iloc[t]
        prev_close = self.prev_close.iloc[t]

        # ---- Position management (exits take priority over new entries) ----
        if state.side == "long":
            if low <= state.stop_price:
                signals.append(Signal(action="stop_long", price=state.stop_price))
                return signals
            if self._exit_hit("long", t, close, rsi, exit_sma, prev_close, state):
                signals.append(Signal(action="exit_long", price=close))
            return signals

        if state.side == "short":
            if high >= state.stop_price:
                signals.append(Signal(action="stop_short", price=state.stop_price))
                return signals
            if self._exit_hit("short", t, close, rsi, exit_sma, prev_close, state):
                signals.append(Signal(action="exit_short", price=close))
            return signals

        # ---- Entries (only when flat) ----
        if pd.isna(rsi):
            return signals

        habilitar_long = bool(params.get("habilitar_long", True))
        habilitar_short = bool(params.get("habilitar_short", False))
        rsi_entry_long = float(params.get("rsi_entry_long", 20.0))
        rsi_entry_short = float(params.get("rsi_entry_short", 80.0))
        stop_pct = float(params.get("stop_pct", 0.15))

        long_regime_ok = True
        short_regime_ok = True
        if self.trend_sma is not None:
            sma = self.trend_sma.iloc[t]
            if pd.isna(sma):
                return signals
            long_regime_ok = close > float(sma)
            short_regime_ok = close < float(sma)

        if habilitar_long and long_regime_ok and rsi <= rsi_entry_long:
            stop_long = close * (1.0 - stop_pct)
            signals.append(Signal(action="entry_long", price=close, stop_price=stop_long))
        elif habilitar_short and short_regime_ok and rsi >= rsi_entry_short:
            stop_short = close * (1.0 + stop_pct)
            signals.append(Signal(action="entry_short", price=close, stop_price=stop_short))

        return signals

    def _exit_hit(
        self,
        side: str,
        t: int,
        close: float,
        rsi: float,
        exit_sma: float,
        prev_close: float,
        state: PositionState,
    ) -> bool:
        """Return True if the configured exit rule fires for the open position."""
        params = self.params
        exit_mode = str(params.get("exit_mode", "target"))

        # Time stop (applies in every mode): force exit after max_hold candles.
        # held = current row index - entry fill row index (O(1) via idx_of).
        max_hold = int(params.get("max_hold", 0))
        if max_hold > 0:
            entry_idx = self.idx_of.get(int(state.entry_time))
            if entry_idx is not None and (t - entry_idx) >= max_hold:
                return True

        if exit_mode == "rsi":
            if pd.isna(rsi):
                return False
            if side == "long":
                return rsi >= float(params.get("rsi_exit_long", 60.0))
            return rsi <= float(params.get("rsi_exit_short", 40.0))

        if exit_mode == "sma":
            if pd.isna(exit_sma):
                return False
            if side == "long":
                return close >= float(exit_sma)
            return close <= float(exit_sma)

        if exit_mode == "target":
            target_pct = float(params.get("target_pct", 0.05))
            if side == "long":
                return close >= state.entry_price * (1.0 + target_pct)
            return close <= state.entry_price * (1.0 - target_pct)

        if exit_mode == "first_up":
            if pd.isna(prev_close):
                return False
            if side == "long":
                return close > float(prev_close)
            return close < float(prev_close)

        return False
