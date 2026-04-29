"""Backtest engine: iterates candles chronologically applying strategy signals."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import pandas as pd

from backend.backtest_metrics import compute_backtest_metrics
from backend.download_engine import INTERVAL_MS
from backend.live_tracker import compute_liquidation_price
from backend.metrics_engine import load_candles_df
from backend.strategies import get_strategy
from backend.strategies.base import PositionState, Signal

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    equity_curve: list[float] = field(default_factory=list)
    timestamps: list[int] = field(default_factory=list)
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

    The async wrapper handles the DB read; the synchronous compute path runs in
    a thread (``asyncio.to_thread``) so the event loop keeps serving /healthz
    and other requests while the backtest is iterating candles. Without this
    split, large backtests (≥10k candles, or any strategy with a Python loop in
    init() like the zigzag-based ones) block the loop for tens of seconds and
    trip the liveness probe → SIGKILL (post-#132).
    """
    result = BacktestResult()

    df = await load_candles_df(symbol, interval, start_ms, end_ms)
    if df.empty or len(df) < 2:
        result.error = "Insufficient candle data for backtest"
        return result

    return await asyncio.to_thread(_run_backtest_compute, df, interval, strategy_name, params, initial_capital)


def _run_backtest_compute(
    df: pd.DataFrame,
    interval: str,
    strategy_name: str,
    params: dict,
    initial_capital: float,
) -> BacktestResult:
    """Synchronous core: strategy init + candle iteration. Always run in a thread."""
    result = BacktestResult()

    try:
        strategy = get_strategy(strategy_name)
        strategy.init(params, df)
    except Exception as exc:
        result.error = f"Strategy init failed: {exc}"
        return result

    execution_mode = params.get("modo_ejecucion", "open_next")
    cost_bps = float(params.get("coste_total_bps", 10.0))
    cost_factor = cost_bps / 10_000.0
    # Leverage / maintenance-margin support (#58 Gap 1). When leverage > 1 the
    # engine sizes positions against equity*leverage and computes a per-trade
    # liquidation_price using the same formula as live (compute_liquidation_price).
    # If price crosses liquidation before the strategy's stop, the trade closes
    # at liquidation_price with exit_reason='liquidated'.
    leverage = float(params.get("leverage", 1.0))
    maintenance_margin_pct = float(params.get("maintenance_margin_pct", 0.005))

    equity = initial_capital
    equity_curve: list[float] = []
    timestamps: list[int] = []
    trade_log: list[dict] = []
    state = PositionState()
    pending_entry: Signal | None = None  # deferred to next open
    # Pending exit / stop signal (open_next semantic, #58 Gap 2). Holds the
    # exit/stop signal raised by strategy.on_candle while still in the closing
    # candle; fills at the NEXT candle's open. Liquidations don't queue here
    # — they're intrabar events that fire immediately at liquidation_price.
    pending_exit: Signal | None = None
    entry_equity: float = 0.0  # equity at moment of entry (before entry fee)
    # ``blown`` mirrors live's ``signal_configs.status='blown'``: once a trade
    # closes via liquidation, the account is gone and no new entries fire for
    # the remainder of the run. Without this flag backtest would keep sizing
    # against the leftover ``equity`` (= maintenance margin buffer ≈ a few %)
    # while live freezes the config — divergent trade logs after the first
    # liquidation. The local mark-to-market loop still runs so the equity
    # curve flatlines at the post-liquidation level.
    blown = False

    def _enter(side: str, exec_price: float, sig: Signal, candle: pd.Series) -> tuple[PositionState, float, float]:
        """Build a PositionState for a fresh entry. Returns (state, fee, entry_equity_snapshot)."""
        invested = equity * leverage
        qty = invested / exec_price
        # Entry fee is on full notional (matches live: invested * cost_factor).
        entry_fee = invested * cost_factor
        liq = compute_liquidation_price(
            side=side,
            entry_price=exec_price,
            leverage=leverage,
            maintenance_margin_pct=maintenance_margin_pct,
        )
        new_state = PositionState(
            side=side,
            entry_price=exec_price,
            entry_time=int(candle["open_time"]),
            stop_price=sig.stop_price,
            quantity=qty,
            liquidation_price=liq,
        )
        return new_state, entry_fee, equity

    def _close_trade(exec_price: float, exit_reason: str, exit_candle_idx: int, exit_candle: pd.Series) -> float:
        """Close the open trade at ``exec_price``, append to trade_log, return new equity.

        Used by both the immediate-close path (close_current mode) and the
        deferred path (open_next mode, where the exit fills at the next
        candle's open).
        """
        nonlocal state
        pnl = _compute_pnl(state, exec_price, cost_factor, equity)
        equity_after = equity + pnl
        dur = exit_candle_idx - _find_entry_candle_idx(df, state.entry_time)
        pnl_pct = (pnl / entry_equity * 100) if entry_equity > 0 else 0.0
        trade_log.append(
            {
                "entry_time": state.entry_time,
                "exit_time": int(exit_candle["open_time"]),
                "side": state.side,
                "direction": state.side,
                "entry_price": state.entry_price,
                "exit_price": exec_price,
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl_pct / 100, 6),
                "fees": round(abs(pnl - _compute_pnl_no_fees(state, exec_price)), 4),
                "exit_reason": exit_reason,
                "duration_candles": max(dur, 0),
                "equity_before": round(entry_equity, 4),
                "equity_after": round(equity_after, 4),
                "position_size": round(entry_equity, 4),
            }
        )
        state = PositionState()
        return equity_after

    n = len(df)

    for t in range(n):
        candle = df.iloc[t]
        open_price = float(candle["open"])
        high_price = float(candle["high"])
        low_price = float(candle["low"])
        close_price = float(candle["close"])

        # Execute deferred exit from the previous candle at this candle's open
        # (open_next semantic, #58 Gap 2). Liquidations are intrabar events and
        # never queue here, so this only handles exit_signal / stop_long /
        # stop_short. Note: a pending_exit takes priority over a pending_entry
        # — they can't physically coexist (exit only queues with state=open;
        # entry only queues with state=flat), but if they did the exit goes
        # first and the entry would have to be invalidated. In practice, we
        # only ever have one at a time.
        if pending_exit is not None and execution_mode == "open_next":
            sig = pending_exit
            pending_exit = None
            normalized = "exit_signal" if sig.action in ("exit_long", "exit_short") else sig.action
            equity = _close_trade(open_price, normalized, t, candle)

        # Execute deferred entry from previous candle at current open
        if pending_entry is not None and execution_mode == "open_next":
            sig = pending_entry
            pending_entry = None
            if sig.action in ("entry_long", "entry_short"):
                side = "long" if sig.action == "entry_long" else "short"
                state, fee, entry_equity = _enter(side, open_price, sig, candle)
                equity -= fee

        # Get signals from strategy
        signals = strategy.on_candle(t, candle, state)

        # Apply stop-moves first so a same-candle exit uses the updated stop.
        for sig in signals:
            if sig.action != "move_stop" or state.side == "flat" or sig.stop_price <= 0:
                continue
            tightens = (state.side == "long" and sig.stop_price > state.stop_price) or (
                state.side == "short" and sig.stop_price < state.stop_price
            )
            if tightens:
                state.stop_price = sig.stop_price

        exit_executed = False

        # Intrabar liquidation check (#58 Gap 1). Mirrors live_tracker: if price
        # crosses liquidation_price BEFORE the stop, the trade closes at the
        # liquidation price with exit_reason='liquidated'. Skipped for
        # unleveraged trades (state.liquidation_price is None). Liquidations
        # never defer (open_next doesn't apply) — the exchange would close
        # the position intrabar.
        if state.side != "flat" and state.liquidation_price is not None:
            liq = state.liquidation_price
            liquidated = (state.side == "long" and low_price <= liq) or (state.side == "short" and high_price >= liq)
            if liquidated:
                equity = _close_trade(liq, "liquidated", t, candle)
                exit_executed = True
                blown = True
                # Drop any pending_entry / pending_exit — the account is blown,
                # those signals are dead.
                pending_entry = None
                pending_exit = None
                result.liquidated = True

        for sig in signals:
            if exit_executed:
                break
            if sig.action in ("stop_long", "stop_short", "exit_long", "exit_short") and state.side != "flat":
                if execution_mode == "open_next":
                    # Defer to the next candle's open (#58 Gap 2). The exit
                    # signal is queued here; no equity / log change yet.
                    # exit_executed=True so we don't process an entry signal
                    # on this same candle.
                    pending_exit = sig
                    exit_executed = True
                    break

                # close_current: fill immediately on this candle.
                # For stops with a gap (open already past stop), use open.
                if sig.action == "stop_long":
                    exec_price = min(sig.price, open_price) if open_price < state.stop_price else sig.price
                    exit_reason = "stop_long"
                elif sig.action == "stop_short":
                    exec_price = max(sig.price, open_price) if open_price > state.stop_price else sig.price
                    exit_reason = "stop_short"
                else:
                    exec_price = close_price
                    exit_reason = sig.action  # exit_long | exit_short

                equity = _close_trade(exec_price, exit_reason, t, candle)
                exit_executed = True
                break

            # Bankruptcy check
            if equity <= 0:
                result.liquidated = True
                result.equity_curve = equity_curve
                result.timestamps = timestamps
                result.trade_log = trade_log
                interval_ms = INTERVAL_MS.get(interval, 86_400_000)
                result.summary = compute_backtest_metrics(equity_curve, trade_log, initial_capital, interval_ms)
                return result

        # Handle entry signals (only if no exit happened, flat, and not blown)
        if not exit_executed and state.side == "flat" and not blown:
            for sig in signals:
                if sig.action in ("entry_long", "entry_short"):
                    if execution_mode == "open_next":
                        pending_entry = sig
                    else:
                        # close_current: execute at close — same sizing helper
                        # as open_next so leverage + liquidation_price land
                        # consistently on both fill paths.
                        side = "long" if sig.action == "entry_long" else "short"
                        state, fee, entry_equity = _enter(side, close_price, sig, candle)
                        equity -= fee
                    break

        # Mark-to-market equity
        if state.side == "long":
            mtm_equity = equity + state.quantity * (close_price - state.entry_price)
        elif state.side == "short":
            mtm_equity = equity + state.quantity * (state.entry_price - close_price)
        else:
            mtm_equity = equity
        equity_curve.append(mtm_equity)
        timestamps.append(int(candle["open_time"]))

    result.equity_curve = equity_curve
    result.timestamps = timestamps
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
