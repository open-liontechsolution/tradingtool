"""Backtest engine: iterates candles chronologically applying strategy signals."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backend.metrics_engine import load_candles_df
from backend.strategies.base import PositionState, Signal
from backend.strategies import get_strategy
from backend.backtest_metrics import compute_backtest_metrics
from backend.download_engine import INTERVAL_MS

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    equity_curve: list[float] = field(default_factory=list)
    trade_log: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    liquidated: bool = False
    error: str | None = None


async def run_backtest(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    strategy_name: str,
    params: dict,
    initial_capital: float = 10_000.0,
) -> BacktestResult:
    """
    Run a backtest for a given symbol/interval/range using the specified strategy.
    """
    result = BacktestResult()

    # Load candles
    df = await load_candles_df(symbol, interval, start_ms, end_ms)
    if df.empty or len(df) < 2:
        result.error = "Insufficient candle data for backtest"
        return result

    # Initialize strategy
    try:
        strategy = get_strategy(strategy_name)
        strategy.init(params, df)
    except Exception as exc:
        result.error = f"Strategy init failed: {exc}"
        return result

    execution_mode = params.get("modo_ejecucion", "open_next")
    cost_bps = float(params.get("coste_total_bps", 10.0))
    cost_factor = cost_bps / 10_000.0

    equity = initial_capital
    equity_curve: list[float] = []
    trade_log: list[dict] = []
    state = PositionState()
    pending_entry: Signal | None = None  # deferred to next open

    n = len(df)

    for t in range(n):
        candle = df.iloc[t]
        open_price = float(candle["open"])
        close_price = float(candle["close"])

        # Execute deferred entry from previous candle at current open
        if pending_entry is not None and execution_mode == "open_next":
            sig = pending_entry
            pending_entry = None
            if sig.action == "entry_long":
                exec_price = open_price
                qty = equity / exec_price
                fee = equity * cost_factor
                equity -= fee
                state = PositionState(
                    side="long",
                    entry_price=exec_price,
                    entry_time=int(candle["open_time"]),
                    stop_price=sig.stop_price,
                    quantity=qty,
                )
            elif sig.action == "entry_short":
                exec_price = open_price
                qty = equity / exec_price
                fee = equity * cost_factor
                equity -= fee
                state = PositionState(
                    side="short",
                    entry_price=exec_price,
                    entry_time=int(candle["open_time"]),
                    stop_price=sig.stop_price,
                    quantity=qty,
                )

        # Get signals from strategy
        signals = strategy.on_candle(t, candle, state)

        exit_executed = False
        for sig in signals:
            if sig.action in ("stop_long", "stop_short", "exit_long", "exit_short"):
                if state.side != "flat":
                    # For stops: use stop_price, but if open is already past stop, use open
                    if sig.action == "stop_long":
                        exec_price = min(sig.price, open_price) if open_price < state.stop_price else sig.price
                    elif sig.action == "stop_short":
                        exec_price = max(sig.price, open_price) if open_price > state.stop_price else sig.price
                    else:
                        exec_price = close_price if execution_mode == "close_current" else open_price

                    pnl = _compute_pnl(state, exec_price, cost_factor, equity)
                    equity += pnl
                    dur = t - _find_entry_candle_idx(df, state.entry_time)
                    trade_log.append({
                        "entry_time": state.entry_time,
                        "exit_time": int(candle["open_time"]),
                        "side": state.side,
                        "entry_price": state.entry_price,
                        "exit_price": exec_price,
                        "pnl": round(pnl, 4),
                        "fees": round(abs(pnl - _compute_pnl_no_fees(state, exec_price)), 4),
                        "exit_reason": sig.action,
                        "duration_candles": max(dur, 0),
                    })
                    state = PositionState()
                    exit_executed = True
                    break

            # Bankruptcy check
            if equity <= 0:
                result.liquidated = True
                result.equity_curve = equity_curve
                result.trade_log = trade_log
                interval_ms = INTERVAL_MS.get(interval, 86_400_000)
                result.summary = compute_backtest_metrics(
                    equity_curve, trade_log, initial_capital, interval_ms
                )
                return result

        # Handle entry signals (only if no exit happened and flat)
        if not exit_executed and state.side == "flat":
            for sig in signals:
                if sig.action in ("entry_long", "entry_short"):
                    if execution_mode == "open_next":
                        pending_entry = sig
                    else:
                        # close_current: execute at close
                        exec_price = close_price
                        qty = equity / exec_price
                        fee = equity * cost_factor
                        equity -= fee
                        state = PositionState(
                            side="long" if sig.action == "entry_long" else "short",
                            entry_price=exec_price,
                            entry_time=int(candle["open_time"]),
                            stop_price=sig.stop_price,
                            quantity=qty,
                        )
                    break

        # Mark-to-market equity
        if state.side == "long":
            mtm_equity = equity + state.quantity * (close_price - state.entry_price)
        elif state.side == "short":
            mtm_equity = equity + state.quantity * (state.entry_price - close_price)
        else:
            mtm_equity = equity
        equity_curve.append(mtm_equity)

    result.equity_curve = equity_curve
    result.trade_log = trade_log
    interval_ms = INTERVAL_MS.get(interval, 86_400_000)
    result.summary = compute_backtest_metrics(equity_curve, trade_log, initial_capital, interval_ms)
    return result


def _compute_pnl(state: PositionState, exec_price: float, cost_factor: float, equity: float) -> float:
    if state.side == "long":
        gross = state.quantity * (exec_price - state.entry_price)
    else:
        gross = state.quantity * (state.entry_price - exec_price)
    fee = abs(state.quantity * exec_price) * cost_factor
    return gross - fee


def _compute_pnl_no_fees(state: PositionState, exec_price: float) -> float:
    if state.side == "long":
        return state.quantity * (exec_price - state.entry_price)
    return state.quantity * (state.entry_price - exec_price)


def _find_entry_candle_idx(df: pd.DataFrame, entry_time: int) -> int:
    matches = df.index[df["open_time"] == entry_time]
    return int(matches[0]) if len(matches) > 0 else 0
