"""Signal scanner: detects entry signals on closed candles using strategy plugins."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from backend.database import get_db
from backend.download_engine import INTERVAL_MS, ensure_candles
from backend.metrics_engine import load_candles_df
from backend.strategies import get_strategy
from backend.strategies.base import PositionState

logger = logging.getLogger(__name__)

# Offset (seconds) after theoretical candle close before scanning.
# Gives time for the candle to appear in the DB.
SCAN_OFFSET_S: dict[str, int] = {
    "1h": 30,
    "4h": 30,
    "1d": 60,
    "3d": 60,
    "1w": 120,
    "1M": 120,
}
DEFAULT_SCAN_OFFSET_S = 30

# How many candles to load for strategy warmup (N_entrada + M_salida + margin)
WARMUP_CANDLES = 600

# Minimum historical range to guarantee reliable strategy signals (365 days in ms)
MIN_HISTORY_MS = 365 * 86_400_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """Load all active signal configs from DB."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM signal_configs WHERE active = 1"
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in rows]


async def _signal_exists(config_id: int, trigger_candle_time: int) -> bool:
    """Check if a signal already exists for this config+candle (dedup)."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT 1 FROM signals WHERE config_id = ? AND trigger_candle_time = ?",
            (config_id, trigger_candle_time),
        )
        return await cursor.fetchone() is not None


async def _update_last_processed(config_id: int, candle_time: int) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE signal_configs SET last_processed_candle = ?, updated_at = ? WHERE id = ?",
            (candle_time, _now_iso(), config_id),
        )
        await db.commit()


async def _create_signal_and_sim_trade(
    config: dict,
    side: str,
    trigger_candle_time: int,
    stop_price: float,
    stop_cross_pct: float,
) -> int | None:
    """Create a signal + sim_trade pair. Returns signal_id or None if dedup."""
    now = _now_iso()

    # Compute stop_trigger from stop_base and stop_cross_pct
    if side == "long":
        stop_trigger = stop_price * (1.0 - stop_cross_pct)
    else:
        stop_trigger = stop_price * (1.0 + stop_cross_pct)

    # Resolve portfolio/invested/leverage
    portfolio = float(config["portfolio"])
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

    cost_bps = float(config["cost_bps"])

    async with get_db() as db:
        try:
            cursor = await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price, stop_trigger_price,
                     status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    config["id"], config["symbol"], config["interval"],
                    config["strategy"], side, trigger_candle_time,
                    stop_price, stop_trigger, now,
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
                 stop_base, stop_trigger, status,
                 portfolio, invested_amount, leverage, fees,
                 created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_entry',
                       ?, ?, ?, ?, ?, ?)""",
            (
                signal_id, config["id"], config["symbol"], config["interval"],
                side, stop_price, stop_trigger,
                portfolio, invested_amount, leverage, 0.0,
                now, now,
            ),
        )
        await db.commit()

    logger.info(
        "Signal created: config=%d side=%s candle=%d stop=%.6f trigger=%.6f",
        config["id"], side, trigger_candle_time, stop_price, stop_trigger,
    )
    return signal_id


async def scan_config(config: dict) -> None:
    """Scan a single config for new signals on the latest closed candle."""
    interval = config["interval"]
    symbol = config["symbol"]
    strategy_name = config["strategy"]
    params: dict = json.loads(config["params"]) if isinstance(config["params"], str) else config["params"]
    stop_cross_pct = float(config["stop_cross_pct"])
    last_processed = config["last_processed_candle"] or 0

    last_closed = _last_closed_candle_time(interval)

    if last_closed <= last_processed:
        return  # already processed

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
            symbol, interval,
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
            last_closed, symbol, interval, int(df.iloc[-1]["open_time"]),
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
            await _create_signal_and_sim_trade(
                config=config,
                side=side,
                trigger_candle_time=last_closed,
                stop_price=sig.stop_price,
                stop_cross_pct=stop_cross_pct,
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
