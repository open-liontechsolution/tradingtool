"""Live tracker: monitors open SimTrades for intrabar stop and candle-close exits."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime

from backend.binance_client import binance_client
from backend.database import get_db
from backend.download_engine import INTERVAL_MS, ensure_candles
from backend.metrics_engine import load_candles_df
from backend.notifications import notify_event
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
    return datetime.now(UTC).isoformat()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _current_candle_open(interval: str) -> int:
    """Return the open_time (ms) of the currently forming candle."""
    step_ms = INTERVAL_MS.get(interval)
    if step_ms is None:
        raise ValueError(f"Unknown interval: {interval}")
    now_ms = _now_ms()
    return (now_ms // step_ms) * step_ms


def _compute_liquidation_price(
    *, side: str, entry_price: float, leverage: float, maintenance_margin_pct: float
) -> float | None:
    """Approximate isolated-margin liquidation price.

    Formula (isolated margin, single position):
      long:  liq = entry × (1 − 1/leverage + mm)
      short: liq = entry × (1 + 1/leverage − mm)

    Returns ``None`` when leverage ≤ 1 (no liquidation risk under isolated
    margin: the worst case is a 100% drawdown, which is already capped by
    ``current_portfolio`` going to zero — handled by the blown-state path).
    """
    if leverage is None or leverage <= 1.0 or entry_price <= 0:
        return None
    factor = 1.0 / leverage - maintenance_margin_pct
    if factor <= 0:
        # mm so high it would liquidate immediately — config is malformed.
        return None
    if side == "long":
        return entry_price * (1.0 - factor)
    if side == "short":
        return entry_price * (1.0 + factor)
    return None


async def _apply_pnl_to_equity(db, config_id: int, net_pnl: float, now: str) -> None:
    """Add ``net_pnl`` to ``signal_configs.current_portfolio`` for ``config_id``.

    Caller is expected to be inside an open ``async with get_db() as db`` block
    so this runs in the same transaction as the sim_trade close.

    Negative results are tolerated at write-time (the SQL is bare arithmetic).
    A separate clamp + blown-state transition happens via ``_maybe_mark_blown``
    so it can also run for manual closes from ``signal_routes`` without
    duplicating the logic.
    """
    await db.execute(
        "UPDATE signal_configs SET current_portfolio = current_portfolio + ?, updated_at = ? WHERE id = ?",
        (net_pnl, now, config_id),
    )


async def _maybe_mark_blown(config_id: int, now: str) -> None:
    """Clamp ``current_portfolio`` at 0 and flip ``status`` to ``'blown'`` if needed.

    Reads the freshly-updated equity in a separate transaction so this runs
    AFTER the sim_trade close commit. Emits ``account_blown`` once on
    transition (idempotent: re-running is a no-op since status is already
    ``blown``).
    """
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT current_portfolio, status, blown_at FROM signal_configs WHERE id = ?",
            (config_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return
        current = float(row[0] or 0.0)
        status = row[1]
        already_blown = status == "blown" or row[2] is not None

        if already_blown:
            return
        if current > 0:
            return

        await db.execute(
            "UPDATE signal_configs SET current_portfolio = 0, status = 'blown', "
            "blown_at = ?, updated_at = ? WHERE id = ?",
            (now, now, config_id),
        )
        await db.commit()

    logger.warning("Config %d marked as BLOWN (current_portfolio reached 0)", config_id)
    await notify_event(
        event_type="account_blown",
        config_id=config_id,
        reference_type="signal_config",
        reference_id=config_id,
        payload={"config_id": config_id, "blown_at": now},
    )


def _get_poll_interval(config: dict) -> int:
    """Determine polling interval for a config, with rate-limit backoff."""
    override = config.get("polling_interval_s")
    base = int(override) if override else DEFAULT_POLL_INTERVAL.get(config["interval"], DEFAULT_POLL_FALLBACK)

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

    Honours ``modo_ejecucion`` from the strategy params:
      * ``open_next`` (default): entry fills at the Open of the candle that
        started AFTER the signal; ``entry_time = trigger_candle_time + step_ms``.
      * ``close_current``: entry fills at the Close of the trigger candle
        itself (already in DB when signal_engine created the sim_trade);
        ``entry_time = trigger_candle_time``. Mirrors ``backtest_engine``.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT st.id, st.symbol, st.interval, st.side, st.signal_id,
                      st.invested_amount, st.portfolio, st.leverage,
                      st.stop_base, st.config_id,
                      s.trigger_candle_time,
                      sc.cost_bps, sc.strategy, sc.params, sc.maintenance_margin_pct
               FROM sim_trades st
               JOIN signals s ON st.signal_id = s.id
               JOIN signal_configs sc ON st.config_id = sc.id
               WHERE st.status = 'pending_entry'"""
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    pending = [dict(zip(cols, row, strict=False)) for row in rows]

    if not pending:
        return

    for trade in pending:
        interval = trade["interval"]
        step_ms = INTERVAL_MS.get(interval)
        if step_ms is None:
            continue

        trigger_time = trade["trigger_candle_time"]
        next_candle_open = trigger_time + step_ms

        try:
            params = json.loads(trade["params"]) if trade.get("params") else {}
        except (TypeError, json.JSONDecodeError):
            params = {}
        execution_mode = params.get("modo_ejecucion", "open_next")
        if execution_mode not in ("open_next", "close_current"):
            logger.warning(
                "SimTrade %d: unknown modo_ejecucion=%r, falling back to open_next",
                trade["id"],
                execution_mode,
            )
            execution_mode = "open_next"

        # Trigger async sync for the entry candle range if missing
        # (only the 2-candle window around the trigger; full history is handled by scanner)
        await ensure_candles(
            trade["symbol"],
            interval,
            trigger_time,
            next_candle_open + step_ms,
        )

        entry_price: float | None = None
        entry_time: int

        if execution_mode == "close_current":
            # Fill at trigger candle's close — always in DB because signal_engine
            # read it to generate the signal.
            async with get_db() as db:
                cursor = await db.execute(
                    "SELECT close FROM klines WHERE symbol = ? AND interval = ? AND open_time = ?",
                    (trade["symbol"], interval, trigger_time),
                )
                row = await cursor.fetchone()
            if row is None:
                logger.warning(
                    "SimTrade %d: trigger candle %d missing in klines for %s %s — retrying next cycle",
                    trade["id"],
                    trigger_time,
                    trade["symbol"],
                    interval,
                )
                continue
            entry_price = float(row[0])
            entry_time = trigger_time
        else:
            # open_next: wait for the next candle to open and fill at its open.
            async with get_db() as db:
                cursor = await db.execute(
                    "SELECT open FROM klines WHERE symbol = ? AND interval = ? AND open_time = ?",
                    (trade["symbol"], interval, next_candle_open),
                )
                row = await cursor.fetchone()

            if row is not None:
                entry_price = float(row[0])
            elif _now_ms() >= next_candle_open + 5000:  # 5s grace
                try:
                    entry_price = await binance_client.get_ticker_price(trade["symbol"])
                except Exception as exc:
                    logger.warning("Could not get ticker for pending entry fill: %s", exc)
                    continue

            if entry_price is None:
                continue  # not time yet
            entry_time = next_candle_open

        invested = float(trade["invested_amount"])
        cost_bps = float(trade["cost_bps"])
        cost_factor = cost_bps / 10_000.0
        fee = invested * cost_factor
        quantity = invested / entry_price
        leverage = float(trade.get("leverage") or 1.0)
        mm_pct = float(trade.get("maintenance_margin_pct") or 0.005)
        liquidation_price = _compute_liquidation_price(
            side=trade["side"],
            entry_price=entry_price,
            leverage=leverage,
            maintenance_margin_pct=mm_pct,
        )
        now = _now_iso()

        async with get_db() as db:
            await db.execute(
                """UPDATE sim_trades
                   SET entry_price = ?, entry_time = ?, quantity = ?, fees = ?,
                       equity_peak = ?, liquidation_price = ?,
                       status = 'open', updated_at = ?
                   WHERE id = ?""",
                (
                    entry_price,
                    entry_time,
                    quantity,
                    fee,
                    float(trade["portfolio"]),
                    liquidation_price,
                    now,
                    trade["id"],
                ),
            )
            await db.execute(
                "UPDATE signals SET status = 'active' WHERE id = ?",
                (trade["signal_id"],),
            )
            await db.commit()

        logger.info(
            "SimTrade %d filled: %s %s entry=%.6f qty=%.6f",
            trade["id"],
            trade["side"],
            trade["symbol"],
            entry_price,
            quantity,
        )

        await notify_event(
            event_type="entry",
            config_id=trade["config_id"],
            reference_type="sim_trade",
            reference_id=trade["id"],
            payload={
                "symbol": trade["symbol"],
                "interval": trade["interval"],
                "side": trade["side"],
                "strategy": trade["strategy"],
                "entry_price": entry_price,
                "stop_price": float(trade["stop_base"]),
                "invested_amount": invested,
                "leverage": leverage,
                "liquidation_price": liquidation_price,
                "sim_trade_id": trade["id"],
            },
        )


# ---------------------------------------------------------------------------
# Intrabar stop check
# ---------------------------------------------------------------------------


async def _check_intrabar_stops() -> None:
    """Poll current price and check stop conditions for open SimTrades."""
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT id, symbol, interval, side, entry_price, stop_base,
                      liquidation_price,
                      quantity, portfolio, invested_amount,
                      leverage, fees, config_id, signal_id
               FROM sim_trades WHERE status = 'open'"""
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    open_trades = [dict(zip(cols, row, strict=False)) for row in rows]

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

        side = trade["side"]
        stop_base = float(trade["stop_base"])
        liq_price = trade.get("liquidation_price")
        liq_price = float(liq_price) if liq_price is not None else None

        # Liquidation has priority over stop: a leveraged exchange would close
        # the position at liq_price *before* a stop-market further away ever
        # fires. With no leverage (liq_price is None), only the stop applies.
        liq_triggered = liq_price is not None and (
            (side == "long" and price <= liq_price) or (side == "short" and price >= liq_price)
        )
        stop_triggered = (side == "long" and price <= stop_base) or (side == "short" and price >= stop_base)

        if not (liq_triggered or stop_triggered):
            continue

        if liq_triggered:
            exec_price = liq_price
            exit_reason = "liquidated"
        else:
            # Stop hit — close at stop_base (gap handling: if price is already
            # past the stop, we use the actual current price instead).
            exec_price = stop_base
            if (side == "long" and price < stop_base) or (side == "short" and price > stop_base):
                exec_price = price
            exit_reason = "stop_intrabar"

        entry_price = float(trade["entry_price"])
        quantity = float(trade["quantity"])
        cost_factor = 0.0  # exit fee
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT cost_bps FROM signal_configs WHERE id = ?",
                (trade["config_id"],),
            )
            cfg_row = await cursor.fetchone()
        if cfg_row:
            cost_factor = float(cfg_row[0]) / 10_000.0

        gross_pnl = quantity * (exec_price - entry_price) if side == "long" else quantity * (entry_price - exec_price)
        exit_fee = abs(quantity * exec_price) * cost_factor
        net_pnl = gross_pnl - exit_fee
        total_fees = float(trade["fees"]) + exit_fee
        portfolio = float(trade["portfolio"])
        pnl_pct = net_pnl / portfolio if portfolio > 0 else 0.0

        async with get_db() as db:
            await db.execute(
                """UPDATE sim_trades
                   SET exit_price = ?, exit_time = ?, exit_reason = ?,
                       status = 'closed', pnl = ?, pnl_pct = ?, fees = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (exec_price, now_ms, exit_reason, net_pnl, pnl_pct, total_fees, now, trade["id"]),
            )
            await db.execute(
                "UPDATE signals SET status = 'closed' WHERE id = ?",
                (trade["signal_id"],),
            )
            await _apply_pnl_to_equity(db, trade["config_id"], net_pnl, now)
            await db.commit()

        # Drives the blown-state transition + account_blown notification when
        # current_portfolio drops to 0 (clamp + status='blown').
        await _maybe_mark_blown(trade["config_id"], now)

        logger.info(
            "SimTrade %d %s: %s %s exec=%.6f pnl=%.4f",
            trade["id"],
            "LIQUIDATED" if liq_triggered else "STOPPED",
            side,
            trade["symbol"],
            exec_price,
            net_pnl,
        )

        if liq_triggered:
            await notify_event(
                event_type="liquidated",
                config_id=trade["config_id"],
                reference_type="sim_trade",
                reference_id=trade["id"],
                payload={
                    "symbol": trade["symbol"],
                    "interval": trade["interval"],
                    "side": side,
                    "exit_price": exec_price,
                    "pnl": net_pnl,
                    "pnl_pct": pnl_pct,
                    "sim_trade_id": trade["id"],
                },
            )
        else:
            await notify_event(
                event_type="stop_hit",
                config_id=trade["config_id"],
                reference_type="sim_trade",
                reference_id=trade["id"],
                payload={
                    "symbol": trade["symbol"],
                    "interval": trade["interval"],
                    "side": side,
                    "exit_price": exec_price,
                    "pnl": net_pnl,
                    "pnl_pct": pnl_pct,
                    "exit_reason": "stop_intrabar",
                    "sim_trade_id": trade["id"],
                },
            )


# ---------------------------------------------------------------------------
# Stop-move handling (trailing stop)
# ---------------------------------------------------------------------------


async def _apply_stop_moves(
    trade: dict,
    signals: list,
    candle_open_time: int,
    state: PositionState,
) -> None:
    """Apply every ``move_stop`` signal against the open trade.

    For each accepted move: tighten stop_base on sim_trades, append a row to
    sim_trade_stop_moves, mirror it onto ``state`` so later exit checks in the
    same candle see the updated value, and emit a notify_event.

    Rejects any move that would loosen the stop (long: new <= current; short:
    new >= current).
    """
    # Collect only tightening move_stop signals with a positive level.
    moves = [s for s in signals if getattr(s, "action", None) == "move_stop" and s.stop_price > 0]
    if not moves:
        return

    side = trade["side"]
    now = _now_iso()
    for sig in moves:
        prev_base = float(trade["stop_base"])
        new_base = float(sig.stop_price)

        tightens = (side == "long" and new_base > prev_base) or (side == "short" and new_base < prev_base)
        if not tightens:
            logger.warning(
                "SimTrade %d move_stop ignored (loosens stop): side=%s prev=%.6f new=%.6f",
                trade["id"],
                side,
                prev_base,
                new_base,
            )
            continue

        async with get_db() as db:
            await db.execute(
                """UPDATE sim_trades
                   SET stop_base = ?, updated_at = ?
                   WHERE id = ?""",
                (new_base, now, trade["id"]),
            )
            cursor = await db.execute(
                """INSERT INTO sim_trade_stop_moves
                       (sim_trade_id, prev_stop_base, new_stop_base,
                        candle_time, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    trade["id"],
                    prev_base,
                    new_base,
                    candle_open_time,
                    now,
                ),
            )
            move_id = cursor.lastrowid
            await db.commit()

        # Mirror onto in-memory trade + PositionState so subsequent signals align.
        trade["stop_base"] = new_base
        state.stop_price = new_base

        entry_price = float(trade["entry_price"]) if trade.get("entry_price") else 0.0
        locked_pct: float | None = None
        if entry_price > 0:
            raw = (new_base - entry_price) / entry_price
            locked_pct = raw if side == "long" else -raw

        await notify_event(
            event_type="stop_moved",
            config_id=trade["config_id"],
            reference_type="sim_trade_stop_move",
            reference_id=int(move_id) if move_id is not None else trade["id"],
            payload={
                "symbol": trade["symbol"],
                "interval": trade["interval"],
                "side": side,
                "prev_stop": prev_base,
                "new_stop": new_base,
                "locked_pct": locked_pct,
                "sim_trade_id": trade["id"],
            },
        )

        logger.info(
            "SimTrade %d STOP MOVED: side=%s %.6f -> %.6f",
            trade["id"],
            side,
            prev_base,
            new_base,
        )


# ---------------------------------------------------------------------------
# Candle-close exit check
# ---------------------------------------------------------------------------


async def _check_candle_close_exits(interval: str | None = None) -> None:
    """On new closed candle, evaluate exit signals using the strategy.

    When ``interval`` is provided, only trades on that timeframe are evaluated.
    """
    base_query = """SELECT st.id, st.symbol, st.interval, st.side, st.entry_price,
                      st.entry_time, st.stop_base,
                      st.quantity, st.portfolio, st.invested_amount, st.fees,
                      st.config_id, st.signal_id,
                      sc.params, sc.strategy, sc.cost_bps
               FROM sim_trades st
               JOIN signal_configs sc ON st.config_id = sc.id
               WHERE st.status = 'open'"""

    async with get_db() as db:
        if interval is not None:
            cursor = await db.execute(base_query + " AND st.interval = ?", (interval,))
        else:
            cursor = await db.execute(base_query)
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    open_trades = [dict(zip(cols, row, strict=False)) for row in rows]

    if not open_trades:
        return

    # Group by (symbol, interval, strategy, params) to batch strategy evals
    groups: dict[tuple, list[dict]] = {}
    for trade in open_trades:
        key = (trade["symbol"], trade["interval"], trade["strategy"], trade["params"])
        groups.setdefault(key, []).append(trade)

    now = _now_iso()

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
                symbol,
                interval,
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
            # Build position state matching the open trade. Use stop_base so the
            # strategy's candle-close stop check fires at the same price as the
            # intrabar poller (both compare against stop_base since #49).
            state = PositionState(
                side=trade["side"],
                entry_price=float(trade["entry_price"]),
                entry_time=int(trade["entry_time"]),
                stop_price=float(trade["stop_base"]),
                quantity=float(trade["quantity"]),
            )

            signals = strategy.on_candle(t_last, candle, state)

            # Apply stop-moves first so a same-candle exit uses the updated stop.
            await _apply_stop_moves(trade, signals, int(candle["open_time"]), state)

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
                            (exec_price, int(candle["open_time"]), net_pnl, pnl_pct, total_fees, now, trade["id"]),
                        )
                        await db.execute(
                            "UPDATE signals SET status = 'closed' WHERE id = ?",
                            (trade["signal_id"],),
                        )
                        await _apply_pnl_to_equity(db, trade["config_id"], net_pnl, now)
                        await db.commit()

                    await _maybe_mark_blown(trade["config_id"], now)

                    duration = max(
                        (int(candle["open_time"]) - int(trade["entry_time"])) // step_ms,
                        0,
                    )
                    await notify_event(
                        event_type="exit_signal",
                        config_id=trade["config_id"],
                        reference_type="sim_trade",
                        reference_id=trade["id"],
                        payload={
                            "symbol": trade["symbol"],
                            "interval": trade["interval"],
                            "side": trade["side"],
                            "exit_price": exec_price,
                            "pnl": net_pnl,
                            "pnl_pct": pnl_pct,
                            "duration_candles": duration,
                            "sim_trade_id": trade["id"],
                        },
                    )

                    logger.info(
                        "SimTrade %d EXIT: %s %s exec=%.6f pnl=%.4f",
                        trade["id"],
                        trade["side"],
                        trade["symbol"],
                        exec_price,
                        net_pnl,
                    )
                    break

                elif sig.action in ("stop_long", "stop_short"):
                    # Stop also detected on candle close via Low/High
                    # But intrabar check should have caught this; handle here
                    # as fallback using the candle's stop_base
                    exec_price = float(trade["stop_base"])
                    open_price = float(candle["open"])

                    # Gap open past stop: execute at open
                    if (
                        trade["side"] == "long"
                        and open_price < exec_price
                        or trade["side"] == "short"
                        and open_price > exec_price
                    ):
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
                                   exit_reason = 'stop_candle',
                                   status = 'closed', pnl = ?, pnl_pct = ?,
                                   fees = ?, updated_at = ?
                               WHERE id = ?""",
                            (exec_price, int(candle["open_time"]), net_pnl, pnl_pct, total_fees, now, trade["id"]),
                        )
                        await db.execute(
                            "UPDATE signals SET status = 'closed' WHERE id = ?",
                            (trade["signal_id"],),
                        )
                        await _apply_pnl_to_equity(db, trade["config_id"], net_pnl, now)
                        await db.commit()

                    await _maybe_mark_blown(trade["config_id"], now)

                    await notify_event(
                        event_type="stop_hit",
                        config_id=trade["config_id"],
                        reference_type="sim_trade",
                        reference_id=trade["id"],
                        payload={
                            "symbol": trade["symbol"],
                            "interval": trade["interval"],
                            "side": trade["side"],
                            "exit_price": exec_price,
                            "pnl": net_pnl,
                            "pnl_pct": pnl_pct,
                            "exit_reason": "stop_candle",
                            "sim_trade_id": trade["id"],
                        },
                    )

                    logger.info(
                        "SimTrade %d STOP (candle): %s %s exec=%.6f pnl=%.4f",
                        trade["id"],
                        trade["side"],
                        trade["symbol"],
                        exec_price,
                        net_pnl,
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
                    await _check_candle_close_exits(interval)

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
                    intervals_needed.append(DEFAULT_POLL_INTERVAL.get(row[0], DEFAULT_POLL_FALLBACK))
            poll = min(intervals_needed) if intervals_needed else DEFAULT_POLL_FALLBACK
        else:
            poll = 30  # idle check every 30s when no trades open

        # Rate-limit soft backoff
        ratio = binance_client.rate_limit.used_weight / max(binance_client.rate_limit.weight_limit, 1)
        if ratio > 0.8:
            poll = max(poll, poll * 2)

        await asyncio.sleep(poll)
