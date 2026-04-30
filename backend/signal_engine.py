"""Signal scanner: detects entry signals on closed candles using strategy plugins."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime

from backend.database import get_db
from backend.download_engine import INTERVAL_MS, ensure_candles
from backend.metrics_engine import load_candles_df
from backend.risk import should_skip_for_max_loss
from backend.strategies import get_strategy
from backend.strategies.base import PositionState

logger = logging.getLogger(__name__)

# How many candles to load for strategy warmup (N_entrada + M_salida + margin)
WARMUP_CANDLES = 600

# Minimum historical range to guarantee reliable strategy signals (365 days in ms)
MIN_HISTORY_MS = 365 * 86_400_000


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _last_closed_candle_time(interval: str) -> int:
    """Return the open_time (ms) of the most recently fully closed candle."""
    step_ms = INTERVAL_MS.get(interval)
    if step_ms is None:
        raise ValueError(f"Unknown interval: {interval}")
    now_ms = _now_ms()
    # Current candle open_time
    current_open = (now_ms // step_ms) * step_ms
    # The last CLOSED candle opened one step before
    return current_open - step_ms


async def _get_active_configs() -> list[dict]:
    """Load all active signal configs from DB.

    A config in ``status='blown'`` is *not* loaded here — it stays inert until
    the user explicitly calls the reset-equity endpoint (#50). The toggleable
    ``active`` flag still gates loading independently.
    """
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM signal_configs WHERE active = 1 AND status != 'blown'")
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row, strict=False)) for row in rows]


async def _has_active_trade(config_id: int) -> bool:
    """Return True if there is already a pending or open sim_trade for this config.

    Includes ``pending_exit`` (open_next mode, #58 Gap 2) — the trade is
    structurally still active until the deferred fill completes. Without this
    inclusion, scan_config could open a parallel entry between the exit
    signal and the fill.
    """
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT 1 FROM sim_trades WHERE config_id = ? AND status IN ('pending_entry', 'open', 'pending_exit')",
            (config_id,),
        )
        return await cursor.fetchone() is not None


async def _has_trade_closed_on_candle(config_id: int, candle_open_time: int) -> bool:
    """Return True if this config closed a sim_trade exactly on this candle.

    Mirrors backtest's per-candle order: when an exit fires, the engine does NOT
    process entry signals on the same candle (``exit_executed`` short-circuits
    the entry block in ``backtest_engine.run_backtest``). Without this guard,
    live opens a fresh position on the same candle that just exited, producing
    one extra trade per same-candle exit/entry pair vs. backtest.
    """
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT 1 FROM sim_trades WHERE config_id = ? AND status = 'closed' AND exit_time = ?",
            (config_id, candle_open_time),
        )
        return await cursor.fetchone() is not None


async def _update_last_processed(config_id: int, candle_time: int) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE signal_configs SET last_processed_candle = ?, updated_at = ? WHERE id = ?",
            (candle_time, _now_iso(), config_id),
        )
        await db.commit()


async def _persist_skipped_signal(
    config: dict,
    side: str,
    trigger_candle_time: int,
    stop_price: float,
) -> None:
    """Record a signal that was suppressed by the max-loss-per-trade filter (#142).

    Inserts a ``signals`` row with ``status='skipped_risk'`` so the user can
    audit how many setups got dropped per config. Does NOT create a sim_trade.
    The existing ``idx_signals_dedup (config_id, trigger_candle_time)`` unique
    index protects against double-insert if scan_config retries this candle.
    """
    now = _now_iso()
    async with get_db() as db:
        try:
            await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price,
                     status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'skipped_risk', ?)""",
                (
                    config["id"],
                    config["symbol"],
                    config["interval"],
                    config["strategy"],
                    side,
                    trigger_candle_time,
                    stop_price,
                    now,
                ),
            )
            await db.commit()
        except Exception:
            logger.debug(
                "Duplicate skipped-risk signal for config %d candle %d",
                config["id"],
                trigger_candle_time,
            )


async def _create_signal_and_sim_trade(
    config: dict,
    side: str,
    trigger_candle_time: int,
    stop_price: float,
) -> int | None:
    """Create a signal + sim_trade pair. Returns signal_id or None if dedup."""
    now = _now_iso()

    # Resolve sizing against the config's *current* portfolio (compounding).
    # ``sim_trades.portfolio`` is the snapshot at entry; ``current_portfolio``
    # on the config evolves as each closed trade applies its PnL.
    portfolio = float(config["current_portfolio"])
    invested_amount = config["invested_amount"]
    leverage = config["leverage"]
    if invested_amount is not None:
        invested_amount = float(invested_amount)
        leverage = invested_amount / portfolio if portfolio > 0 else 1.0
    elif leverage is not None:
        leverage = float(leverage)
        invested_amount = portfolio * leverage
    else:
        leverage = 1.0
        invested_amount = portfolio

    async with get_db() as db:
        try:
            cursor = await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price,
                     status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    config["id"],
                    config["symbol"],
                    config["interval"],
                    config["strategy"],
                    side,
                    trigger_candle_time,
                    stop_price,
                    now,
                ),
            )
            signal_id = cursor.lastrowid
        except Exception:
            # UNIQUE constraint → duplicate
            logger.debug("Duplicate signal for config %d candle %d", config["id"], trigger_candle_time)
            return None

        await db.execute(
            """INSERT INTO sim_trades
                (signal_id, config_id, symbol, interval, side,
                 stop_base, status,
                 portfolio, invested_amount, leverage, fees,
                 created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending_entry',
                       ?, ?, ?, ?, ?, ?)""",
            (
                signal_id,
                config["id"],
                config["symbol"],
                config["interval"],
                side,
                stop_price,
                portfolio,
                invested_amount,
                leverage,
                0.0,
                now,
                now,
            ),
        )
        await db.commit()

    logger.info(
        "Signal created: config=%d side=%s candle=%d stop=%.6f",
        config["id"],
        side,
        trigger_candle_time,
        stop_price,
    )
    return signal_id


async def scan_config(config: dict) -> None:
    """Scan a single config for new signals on the latest closed candle."""
    interval = config["interval"]
    symbol = config["symbol"]
    strategy_name = config["strategy"]
    params: dict = json.loads(config["params"]) if isinstance(config["params"], str) else config["params"]
    last_processed = config["last_processed_candle"] or 0

    # Don't open new positions on a blown account (#50). The user must call
    # POST /api/signals/configs/{id}/reset-equity to bring it back to active.
    if config.get("status") == "blown":
        logger.debug("Config %d is blown — skipping scan", config["id"])
        return

    last_closed = _last_closed_candle_time(interval)

    if last_closed <= last_processed:
        return  # already processed

    # Skip entry signal if there is already an active trade for this config
    if await _has_active_trade(config["id"]):
        logger.debug(
            "Config %d already has an active trade, skipping entry scan for candle %d",
            config["id"],
            last_closed,
        )
        await _update_last_processed(config["id"], last_closed)
        return

    # Skip entry signal if a trade just closed on the same candle (mirror backtest).
    # Only applies to close_current mode: in close_current the exit fires AND fills
    # on the same candle, so backtest's exit_executed short-circuit blocks the
    # entry block in that iteration. In open_next the exit *fires* on candle t
    # but *fills* on t+1.open — backtest's strategy at iter t+1 (post-fill) is
    # free to emit a new entry, and live's scan_config should mirror that.
    # ``_has_active_trade`` (which now includes pending_exit) handles the
    # exit-iteration block in open_next.
    execution_mode = params.get("modo_ejecucion", "open_next")
    if execution_mode == "close_current" and await _has_trade_closed_on_candle(config["id"], last_closed):
        logger.debug(
            "Config %d had a trade close on candle %d, skipping entry scan",
            config["id"],
            last_closed,
        )
        await _update_last_processed(config["id"], last_closed)
        return

    # Load candles for strategy warmup
    step_ms = INTERVAL_MS[interval]
    warmup_start = last_closed - (WARMUP_CANDLES * step_ms)
    history_start = last_closed - MIN_HISTORY_MS
    start_ms = min(warmup_start, history_start)  # at least 365d OR 600 candles
    end_ms = last_closed + step_ms  # inclusive of the last closed candle

    # Ensure all required candles are in DB; launch async sync if not
    ready = await ensure_candles(symbol, interval, start_ms, end_ms)
    if not ready:
        logger.info(
            "ensure_candles: data sync in progress for %s %s, skipping scan cycle",
            symbol,
            interval,
        )
        return

    df = await load_candles_df(symbol, interval, start_ms, end_ms)
    if df.empty or len(df) < 2:
        logger.warning("Insufficient candle data for scan: %s %s", symbol, interval)
        return

    # Check that the last closed candle is actually in the data
    if int(df.iloc[-1]["open_time"]) != last_closed:
        logger.warning(
            "Last closed candle %d not in DB for %s %s (latest: %d). Skipping.",
            last_closed,
            symbol,
            interval,
            int(df.iloc[-1]["open_time"]),
        )
        return

    try:
        strategy = get_strategy(strategy_name)
        strategy.init(params, df)
    except Exception as exc:
        logger.error("Strategy init failed for config %d: %s", config["id"], exc)
        return

    t_last = len(df) - 1
    candle = df.iloc[t_last]
    state = PositionState()  # always flat — we're looking for entries
    signals = strategy.on_candle(t_last, candle, state)

    for sig in signals:
        if sig.action in ("entry_long", "entry_short"):
            side = "long" if sig.action == "entry_long" else "short"

            # Max-loss-per-trade risk filter (#142). Drop entries whose
            # estimated loss-if-stopped (under the configured leverage)
            # would exceed the per-config equity-loss threshold. Reference
            # entry price = trigger candle's close (deterministic at signal
            # time, identical in backtest → keeps parity).
            if config.get("max_loss_per_trade_enabled"):
                lev = config.get("leverage")
                lev = float(lev) if lev is not None else 1.0
                skip, est_loss = should_skip_for_max_loss(
                    entry_price=float(candle["close"]),
                    stop_base=sig.stop_price,
                    side=side,
                    leverage=lev,
                    invested_amount=config.get("invested_amount"),
                    current_portfolio=float(config["current_portfolio"]),
                    max_loss_pct=float(config["max_loss_per_trade_pct"]),
                )
                if skip:
                    logger.info(
                        "Risk filter: skipping config=%d side=%s candle=%d est_loss=%.4f threshold=%.4f",
                        config["id"],
                        side,
                        last_closed,
                        est_loss,
                        float(config["max_loss_per_trade_pct"]),
                    )
                    await _persist_skipped_signal(
                        config=config,
                        side=side,
                        trigger_candle_time=last_closed,
                        stop_price=sig.stop_price,
                    )
                    break

            await _create_signal_and_sim_trade(
                config=config,
                side=side,
                trigger_candle_time=last_closed,
                stop_price=sig.stop_price,
            )
            break  # one signal per scan cycle

    await _update_last_processed(config["id"], last_closed)


async def run_signal_scanner() -> None:
    """Main scanner loop. Runs continuously as a background task."""
    logger.info("Signal scanner started")
    while True:
        try:
            configs = await _get_active_configs()
            for config in configs:
                try:
                    await scan_config(config)
                except Exception as exc:
                    logger.error("Error scanning config %d: %s", config["id"], exc, exc_info=True)
        except asyncio.CancelledError:
            logger.info("Signal scanner cancelled")
            return
        except Exception as exc:
            logger.error("Signal scanner loop error: %s", exc, exc_info=True)

        await asyncio.sleep(15)
