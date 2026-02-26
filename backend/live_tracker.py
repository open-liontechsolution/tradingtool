"""Live tracker: monitors open SimTrades for intrabar stop and candle-close exits."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from backend.binance_client import binance_client, WEIGHT_LIMIT_PER_MINUTE
from backend.database import get_db
from backend.download_engine import INTERVAL_MS, ensure_candles
from backend.metrics_engine import load_candles_df
from backend.strategies import get_strategy
from backend.strategies.base import PositionState

logger = logging.getLogger(__name__)

# Default polling intervals (seconds) per candle interval
DEFAULT_POLL_INTERVAL: dict[str, int] = {
    "1h": 60,
    "2h": 60,
    "4h": 120,
    "6h": 120,
    "8h": 180,
    "12h": 180,
    "1d": 300,
    "3d": 300,
    "1w": 600,
    "1M": 600,
}
DEFAULT_POLL_FALLBACK = 120


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _current_candle_open(interval: str) -> int:
    """Return the open_time (ms) of the currently forming candle."""
    step_ms = INTERVAL_MS.get(interval)
    if step_ms is None:
        raise ValueError(f"Unknown interval: {interval}")
    now_ms = _now_ms()
    return (now_ms // step_ms) * step_ms


def _get_poll_interval(config: dict) -> int:
    """Determine polling interval for a config, with rate-limit backoff."""
    override = config.get("polling_interval_s")
    if override:
        base = int(override)
    else:
        base = DEFAULT_POLL_INTERVAL.get(config["interval"], DEFAULT_POLL_FALLBACK)

    # Soft priority: if weight > 80%, double the interval
    ratio = binance_client.rate_limit.used_weight / max(binance_client.rate_limit.weight_limit, 1)
    if ratio > 0.8:
        base *= 2
    return base


# ---------------------------------------------------------------------------
# Pending entry fill
# ---------------------------------------------------------------------------

async def _fill_pending_entries() -> None:
    """Fill entry_price for SimTrades in pending_entry state.

    The entry price is the Open of the candle that started AFTER the signal.
    Once that candle opens, we take the first available price as a proxy.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT st.id, st.symbol, st.interval, st.side, st.signal_id,
                      st.invested_amount, st.portfolio, st.leverage,
                      s.trigger_candle_time, sc.cost_bps
               FROM sim_trades st
               JOIN signals s ON st.signal_id = s.id
               JOIN signal_configs sc ON st.config_id = sc.id
               WHERE st.status = 'pending_entry'"""
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    pending = [dict(zip(cols, row)) for row in rows]

    if not pending:
        return

    for trade in pending:
        interval = trade["interval"]
        step_ms = INTERVAL_MS.get(interval)
        if step_ms is None:
            continue

        trigger_time = trade["trigger_candle_time"]
        next_candle_open = trigger_time + step_ms

        # Trigger async sync for the entry candle range if missing
        # (only the 2-candle window around the trigger; full history is handled by scanner)
        await ensure_candles(
            trade["symbol"], interval,
            trigger_time, next_candle_open + step_ms,
        )

        # Check if next candle exists in DB (means it has opened and we have data)
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT open FROM klines WHERE symbol = ? AND interval = ? AND open_time = ?",
                (trade["symbol"], interval, next_candle_open),
            )
            row = await cursor.fetchone()

        entry_price: float | None = None
        if row is not None:
            entry_price = float(row[0])
        else:
            # If the next candle hasn't appeared in DB yet, check if we're past
            # its open time — if so, use current ticker as proxy
            if _now_ms() >= next_candle_open + 5000:  # 5s grace
                try:
                    entry_price = await binance_client.get_ticker_price(trade["symbol"])
                except Exception as exc:
                    logger.warning("Could not get ticker for pending entry fill: %s", exc)
                    continue

        if entry_price is None:
            continue  # not time yet

        invested = float(trade["invested_amount"])
        cost_bps = float(trade["cost_bps"])
        cost_factor = cost_bps / 10_000.0
        fee = invested * cost_factor
        quantity = invested / entry_price
        now = _now_iso()

        async with get_db() as db:
            await db.execute(
                """UPDATE sim_trades
                   SET entry_price = ?, entry_time = ?, quantity = ?, fees = ?,
                       equity_peak = ?, status = 'open', updated_at = ?
                   WHERE id = ?""",
                (entry_price, next_candle_open, quantity, fee,
                 float(trade["portfolio"]), now, trade["id"]),
            )
            await db.execute(
                "UPDATE signals SET status = 'active' WHERE id = ?",
                (trade["signal_id"],),
            )
            await db.commit()

        logger.info(
            "SimTrade %d filled: %s %s entry=%.6f qty=%.6f",
            trade["id"], trade["side"], trade["symbol"], entry_price, quantity,
        )


# ---------------------------------------------------------------------------
# Intrabar stop check
# ---------------------------------------------------------------------------

async def _check_intrabar_stops() -> None:
    """Poll current price and check stop conditions for open SimTrades."""
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT id, symbol, interval, side, entry_price, stop_base,
                      stop_trigger, quantity, portfolio, invested_amount,
                      leverage, fees, config_id, signal_id
               FROM sim_trades WHERE status = 'open'"""
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    open_trades = [dict(zip(cols, row)) for row in rows]

    if not open_trades:
        return

    # Group by symbol to minimize ticker calls
    symbols = list({t["symbol"] for t in open_trades})
    prices: dict[str, float] = {}
    for sym in symbols:
        try:
            prices[sym] = await binance_client.get_ticker_price(sym)
        except Exception as exc:
            logger.warning("Ticker fetch failed for %s: %s", sym, exc)

    now = _now_iso()
    now_ms = _now_ms()

    for trade in open_trades:
        price = prices.get(trade["symbol"])
        if price is None:
            continue

        triggered = False
        if trade["side"] == "long" and price <= trade["stop_trigger"]:
            triggered = True
        elif trade["side"] == "short" and price >= trade["stop_trigger"]:
            triggered = True

        if not triggered:
            continue

        # Stop hit — close the SimTrade
        exec_price = trade["stop_trigger"]
        entry_price = float(trade["entry_price"])
        quantity = float(trade["quantity"])
        cost_factor = 0.0  # exit fee
        # Load cost_bps from config
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT cost_bps FROM signal_configs WHERE id = ?",
                (trade["config_id"],),
            )
            cfg_row = await cursor.fetchone()
        if cfg_row:
            cost_factor = float(cfg_row[0]) / 10_000.0

        if trade["side"] == "long":
            gross_pnl = quantity * (exec_price - entry_price)
        else:
            gross_pnl = quantity * (entry_price - exec_price)
        exit_fee = abs(quantity * exec_price) * cost_factor
        net_pnl = gross_pnl - exit_fee
        total_fees = float(trade["fees"]) + exit_fee
        portfolio = float(trade["portfolio"])
        pnl_pct = net_pnl / portfolio if portfolio > 0 else 0.0

        async with get_db() as db:
            await db.execute(
                """UPDATE sim_trades
                   SET exit_price = ?, exit_time = ?, exit_reason = 'stop_intrabar',
                       status = 'closed', pnl = ?, pnl_pct = ?, fees = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (exec_price, now_ms, net_pnl, pnl_pct, total_fees, now, trade["id"]),
            )
            await db.execute(
                "UPDATE signals SET status = 'closed' WHERE id = ?",
                (trade["signal_id"],),
            )
            # Log notification (dedup via unique index)
            try:
                await db.execute(
                    """INSERT INTO notification_log
                        (event_type, reference_type, reference_id, message, sent_at)
                       VALUES ('stop_hit', 'sim_trade', ?, ?, ?)""",
                    (trade["id"],
                     f"Stop hit on {trade['symbol']} {trade['side']} at {exec_price:.6f}",
                     now),
                )
            except Exception:
                pass  # duplicate notification
            await db.commit()

        logger.info(
            "SimTrade %d STOPPED: %s %s exec=%.6f pnl=%.4f",
            trade["id"], trade["side"], trade["symbol"], exec_price, net_pnl,
        )

        # Check liquidation
        if portfolio + net_pnl <= 0:
            logger.warning("SimTrade %d: liquidation event (equity <= 0)", trade["id"])


# ---------------------------------------------------------------------------
# Candle-close exit check
# ---------------------------------------------------------------------------

async def _check_candle_close_exits() -> None:
    """On new closed candle, evaluate exit signals using the strategy."""
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT st.id, st.symbol, st.interval, st.side, st.entry_price,
                      st.entry_time, st.stop_base, st.stop_trigger,
                      st.quantity, st.portfolio, st.invested_amount, st.fees,
                      st.config_id, st.signal_id,
                      sc.params, sc.strategy, sc.cost_bps, sc.stop_cross_pct
               FROM sim_trades st
               JOIN signal_configs sc ON st.config_id = sc.id
               WHERE st.status = 'open'"""
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    open_trades = [dict(zip(cols, row)) for row in rows]

    if not open_trades:
        return

    # Group by (symbol, interval, strategy, params) to batch strategy evals
    groups: dict[tuple, list[dict]] = {}
    for trade in open_trades:
        key = (trade["symbol"], trade["interval"], trade["strategy"], trade["params"])
        groups.setdefault(key, []).append(trade)

    now = _now_iso()
    now_ms_val = _now_ms()

    for (symbol, interval, strategy_name, params_str), trades in groups.items():
        step_ms = INTERVAL_MS.get(interval)
        if step_ms is None:
            continue

        # Determine the last closed candle
        current_open = _current_candle_open(interval)
        last_closed_open = current_open - step_ms

        # Load candles for strategy evaluation
        params = json.loads(params_str) if isinstance(params_str, str) else params_str
        warmup = 600
        start_ms = last_closed_open - (warmup * step_ms)
        end_ms = last_closed_open + step_ms

        # Ensure candles are present; launch async sync if not and skip this group
        ready = await ensure_candles(symbol, interval, start_ms, end_ms)
        if not ready:
            logger.info(
                "live_tracker: data sync in progress for %s %s, skipping exit check",
                symbol, interval,
            )
            continue

        df = await load_candles_df(symbol, interval, start_ms, end_ms)
        if df.empty or len(df) < 2:
            continue

        # Check last candle is the one we expect
        if int(df.iloc[-1]["open_time"]) != last_closed_open:
            continue

        try:
            strategy = get_strategy(strategy_name)
            strategy.init(params, df)
        except Exception as exc:
            logger.error("Strategy init failed for exit check: %s", exc)
            continue

        t_last = len(df) - 1
        candle = df.iloc[t_last]

        for trade in trades:
            # Build position state matching the open trade
            state = PositionState(
                side=trade["side"],
                entry_price=float(trade["entry_price"]),
                entry_time=int(trade["entry_time"]),
                stop_price=float(trade["stop_base"]),
                quantity=float(trade["quantity"]),
            )

            signals = strategy.on_candle(t_last, candle, state)

            for sig in signals:
                if sig.action in ("exit_long", "exit_short"):
                    exec_price = float(candle["close"])
                    entry_price = float(trade["entry_price"])
                    quantity = float(trade["quantity"])
                    cost_factor = float(trade["cost_bps"]) / 10_000.0

                    if trade["side"] == "long":
                        gross_pnl = quantity * (exec_price - entry_price)
                    else:
                        gross_pnl = quantity * (entry_price - exec_price)
                    exit_fee = abs(quantity * exec_price) * cost_factor
                    net_pnl = gross_pnl - exit_fee
                    total_fees = float(trade["fees"]) + exit_fee
                    portfolio = float(trade["portfolio"])
                    pnl_pct = net_pnl / portfolio if portfolio > 0 else 0.0

                    async with get_db() as db:
                        await db.execute(
                            """UPDATE sim_trades
                               SET exit_price = ?, exit_time = ?,
                                   exit_reason = 'exit_signal',
                                   status = 'closed', pnl = ?, pnl_pct = ?,
                                   fees = ?, updated_at = ?
                               WHERE id = ?""",
                            (exec_price, int(candle["open_time"]),
                             net_pnl, pnl_pct, total_fees, now, trade["id"]),
                        )
                        await db.execute(
                            "UPDATE signals SET status = 'closed' WHERE id = ?",
                            (trade["signal_id"],),
                        )
                        try:
                            await db.execute(
                                """INSERT INTO notification_log
                                    (event_type, reference_type, reference_id, message, sent_at)
                                   VALUES ('exit_signal', 'sim_trade', ?, ?, ?)""",
                                (trade["id"],
                                 f"Exit signal on {trade['symbol']} {trade['side']} at {exec_price:.6f}",
                                 now),
                            )
                        except Exception:
                            pass
                        await db.commit()

                    logger.info(
                        "SimTrade %d EXIT: %s %s exec=%.6f pnl=%.4f",
                        trade["id"], trade["side"], trade["symbol"], exec_price, net_pnl,
                    )
                    break

                elif sig.action in ("stop_long", "stop_short"):
                    # Stop also detected on candle close via Low/High
                    # But intrabar check should have caught this; handle here
                    # as fallback using the candle's stop_price
                    exec_price = float(trade["stop_trigger"])
                    open_price = float(candle["open"])

                    # Gap open past stop: execute at open
                    if trade["side"] == "long" and open_price < exec_price:
                        exec_price = open_price
                    elif trade["side"] == "short" and open_price > exec_price:
                        exec_price = open_price

                    entry_price = float(trade["entry_price"])
                    quantity = float(trade["quantity"])
                    cost_factor = float(trade["cost_bps"]) / 10_000.0

                    if trade["side"] == "long":
                        gross_pnl = quantity * (exec_price - entry_price)
                    else:
                        gross_pnl = quantity * (entry_price - exec_price)
                    exit_fee = abs(quantity * exec_price) * cost_factor
                    net_pnl = gross_pnl - exit_fee
                    total_fees = float(trade["fees"]) + exit_fee
                    portfolio = float(trade["portfolio"])
                    pnl_pct = net_pnl / portfolio if portfolio > 0 else 0.0

                    async with get_db() as db:
                        await db.execute(
                            """UPDATE sim_trades
                               SET exit_price = ?, exit_time = ?,
                                   exit_reason = 'stop_intrabar',
                                   status = 'closed', pnl = ?, pnl_pct = ?,
                                   fees = ?, updated_at = ?
                               WHERE id = ?""",
                            (exec_price, int(candle["open_time"]),
                             net_pnl, pnl_pct, total_fees, now, trade["id"]),
                        )
                        await db.execute(
                            "UPDATE signals SET status = 'closed' WHERE id = ?",
                            (trade["signal_id"],),
                        )
                        try:
                            await db.execute(
                                """INSERT INTO notification_log
                                    (event_type, reference_type, reference_id, message, sent_at)
                                   VALUES ('stop_hit', 'sim_trade', ?, ?, ?)""",
                                (trade["id"],
                                 f"Stop hit (candle) on {trade['symbol']} {trade['side']} at {exec_price:.6f}",
                                 now),
                            )
                        except Exception:
                            pass
                        await db.commit()

                    logger.info(
                        "SimTrade %d STOP (candle): %s %s exec=%.6f pnl=%.4f",
                        trade["id"], trade["side"], trade["symbol"], exec_price, net_pnl,
                    )
                    break


# ---------------------------------------------------------------------------
# Main tracker loop
# ---------------------------------------------------------------------------

_last_candle_check: dict[str, int] = {}  # interval -> last checked candle open_time


async def run_live_tracker() -> None:
    """Main tracker loop. Runs continuously as a background task."""
    logger.info("Live tracker started")

    while True:
        try:
            # 1. Fill pending entries
            await _fill_pending_entries()

            # 2. Check intrabar stops (price polling)
            await _check_intrabar_stops()

            # 3. Check candle-close exits (only when a new candle has closed)
            for interval in INTERVAL_MS:
                current_open = _current_candle_open(interval)
                last_checked = _last_candle_check.get(interval, 0)
                if current_open > last_checked:
                    # A new candle has opened → the previous one just closed
                    _last_candle_check[interval] = current_open
                    await _check_candle_close_exits()

        except asyncio.CancelledError:
            logger.info("Live tracker cancelled")
            return
        except Exception as exc:
            logger.error("Live tracker loop error: %s", exc, exc_info=True)

        # Determine shortest poll interval among open trades
        poll = DEFAULT_POLL_FALLBACK
        async with get_db() as db:
            cursor = await db.execute(
                """SELECT DISTINCT st.interval, sc.polling_interval_s
                   FROM sim_trades st
                   JOIN signal_configs sc ON st.config_id = sc.id
                   WHERE st.status IN ('pending_entry', 'open')"""
            )
            rows = await cursor.fetchall()

        if rows:
            intervals_needed = []
            for row in rows:
                override = row[1]
                if override:
                    intervals_needed.append(int(override))
                else:
                    intervals_needed.append(
                        DEFAULT_POLL_INTERVAL.get(row[0], DEFAULT_POLL_FALLBACK)
                    )
            poll = min(intervals_needed) if intervals_needed else DEFAULT_POLL_FALLBACK
        else:
            poll = 30  # idle check every 30s when no trades open

        # Rate-limit soft backoff
        ratio = binance_client.rate_limit.used_weight / max(binance_client.rate_limit.weight_limit, 1)
        if ratio > 0.8:
            poll = max(poll, poll * 2)

        await asyncio.sleep(poll)
