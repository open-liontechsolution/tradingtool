"""Backtest metrics: computes summary statistics from trade log + equity curve."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def compute_backtest_metrics(
    equity_curve: list[float],
    trade_log: list[dict],
    initial_capital: float,
    interval_ms: int,
) -> dict[str, Any]:
    """
    Compute performance metrics from equity curve and trade log.

    Args:
        equity_curve: list of equity values (one per candle)
        trade_log: list of trade dicts with keys: entry_time, exit_time,
                   side, entry_price, exit_price, pnl, fees
        initial_capital: starting capital
        interval_ms: candle duration in milliseconds (for annualization)
    """
    if not equity_curve or initial_capital <= 0:
        return {}

    eq = np.array(equity_curve, dtype=float)
    n_candles = len(eq)

    # --- Net profit ---
    net_profit = eq[-1] - initial_capital
    net_profit_pct = net_profit / initial_capital * 100

    # --- CAGR ---
    candles_per_year = _candles_per_year(interval_ms)
    years = n_candles / candles_per_year if candles_per_year > 0 else 0
    cagr = 0.0
    if years > 0 and eq[-1] > 0:
        cagr = ((eq[-1] / initial_capital) ** (1.0 / years) - 1) * 100

    # --- Max drawdown ---
    running_max = np.maximum.accumulate(eq)
    drawdown_series = (eq - running_max) / running_max * 100
    max_drawdown = float(drawdown_series.min())

    # --- Returns for Sharpe/Sortino ---
    returns = np.diff(eq) / eq[:-1]
    rf = 0.0  # risk-free rate per candle
    excess = returns - rf
    sharpe = 0.0
    sortino = 0.0
    if len(returns) > 1:
        std = float(np.std(excess))
        if std > 0:
            sharpe = float(np.mean(excess) / std * math.sqrt(candles_per_year))
        downside = excess[excess < 0]
        dstd = float(np.std(downside)) if len(downside) > 1 else 0.0
        if dstd > 0:
            sortino = float(np.mean(excess) / dstd * math.sqrt(candles_per_year))

    # --- Trade statistics ---
    n_trades = len(trade_log)
    if n_trades == 0:
        return {
            "net_profit": net_profit,
            "net_profit_pct": net_profit_pct,
            "cagr_pct": cagr,
            "max_drawdown_pct": max_drawdown,
            "sharpe": sharpe,
            "sortino": sortino,
            "n_trades": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": 0.0,
            "time_in_market_pct": 0.0,
            "drawdown_curve": drawdown_series.tolist(),
        }

    pnls = [t.get("pnl", 0.0) for t in trade_log]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / n_trades * 100
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    expectancy = float(np.mean(pnls))
    payoff_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    # Time in market: sum of candles in trades / total candles
    total_in_market = 0
    for t in trade_log:
        dur = t.get("duration_candles", 0)
        total_in_market += dur
    time_in_market_pct = total_in_market / max(n_candles, 1) * 100

    return {
        "net_profit": round(net_profit, 4),
        "net_profit_pct": round(net_profit_pct, 4),
        "cagr_pct": round(cagr, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "n_trades": n_trades,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 4) if not math.isinf(profit_factor) else None,
        "expectancy": round(expectancy, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "payoff_ratio": round(payoff_ratio, 4) if not math.isinf(payoff_ratio) else None,
        "time_in_market_pct": round(time_in_market_pct, 2),
        "drawdown_curve": drawdown_series.tolist(),
    }


def _candles_per_year(interval_ms: int) -> float:
    """Approximate candles per year for the given interval."""
    ms_per_year = 365.25 * 24 * 3600 * 1000
    return ms_per_year / max(interval_ms, 1)
