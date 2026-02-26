"""Download engine: orchestrates Binance klines download with gap detection and upsert."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from backend.binance_client import binance_client, parse_candle, validate_candle
from backend.database import get_db

logger = logging.getLogger(__name__)

# Interval duration in milliseconds
INTERVAL_MS: dict[str, int] = {
    "1m":  60_000,
    "3m":  3 * 60_000,
    "5m":  5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h":  3_600_000,
    "2h":  2 * 3_600_000,
    "4h":  4 * 3_600_000,
    "6h":  6 * 3_600_000,
    "8h":  8 * 3_600_000,
    "12h": 12 * 3_600_000,
    "1d":  86_400_000,
    "3d":  3 * 86_400_000,
    "1w":  7 * 86_400_000,
    "1M":  30 * 86_400_000,  # approximation; Binance uses calendar months
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expected_open_times(start_ms: int, end_ms: int, interval: str) -> list[int]:
    """
    Generate the list of expected candle open_time values for a given range.
    For 1M intervals, we approximate at 30 days.
    """
    step = INTERVAL_MS.get(interval)
    if step is None:
        raise ValueError(f"Unknown interval: {interval}")

    # Align start to interval boundary
    aligned_start = (start_ms // step) * step
    if aligned_start < start_ms:
        aligned_start += step

    times = []
    t = aligned_start
    while t < end_ms:
        times.append(t)
        t += step
    return times


async def _get_existing_open_times(
    db: aiosqlite.Connection, symbol: str, interval: str, start_ms: int, end_ms: int
) -> set[int]:
    cursor = await db.execute(
        "SELECT open_time FROM klines WHERE symbol=? AND interval=? AND open_time>=? AND open_time<?",
        (symbol, interval, start_ms, end_ms),
    )
    rows = await cursor.fetchall()
    return {row[0] for row in rows}


async def _upsert_candles(db: aiosqlite.Connection, candles: list[dict]) -> int:
    if not candles:
        return 0
    await db.executemany(
        """
        INSERT OR REPLACE INTO klines
            (symbol, interval, open_time, open, high, low, close, volume,
             close_time, quote_asset_volume, number_of_trades,
             taker_buy_base_vol, taker_buy_quote_vol, ignore_field,
             source, downloaded_at)
        VALUES
            (:symbol, :interval, :open_time, :open, :high, :low, :close, :volume,
             :close_time, :quote_asset_volume, :number_of_trades,
             :taker_buy_base_vol, :taker_buy_quote_vol, :ignore_field,
             :source, :downloaded_at)
        """,
        candles,
    )
    await db.commit()
    return len(candles)


async def _update_job(
    db: aiosqlite.Connection,
    job_id: int,
    *,
    status: str | None = None,
    progress_pct: float | None = None,
    candles_downloaded: int | None = None,
    candles_expected: int | None = None,
    gaps_found: int | None = None,
    log_entry: str | None = None,
) -> None:
    """Update fields on a download_jobs row."""
    # Fetch current log
    cursor = await db.execute("SELECT log FROM download_jobs WHERE id=?", (job_id,))
    row = await cursor.fetchone()
    if row is None:
        return
    current_log: list = json.loads(row[0] or "[]")
    if log_entry:
        current_log.append({"ts": _now_iso(), "msg": log_entry})

    fields: list[str] = ["updated_at=?", "log=?"]
    values: list[Any] = [_now_iso(), json.dumps(current_log)]

    if status is not None:
        fields.append("status=?")
        values.append(status)
    if progress_pct is not None:
        fields.append("progress_pct=?")
        values.append(progress_pct)
    if candles_downloaded is not None:
        fields.append("candles_downloaded=?")
        values.append(candles_downloaded)
    if candles_expected is not None:
        fields.append("candles_expected=?")
        values.append(candles_expected)
    if gaps_found is not None:
        fields.append("gaps_found=?")
        values.append(gaps_found)

    values.append(job_id)
    await db.execute(f"UPDATE download_jobs SET {', '.join(fields)} WHERE id=?", values)
    await db.commit()


async def create_download_job(
    symbol: str, interval: str, start_time: int, end_time: int
) -> int:
    """Create a new download job and return its ID."""
    async with get_db() as db:
        now = _now_iso()
        cursor = await db.execute(
            """
            INSERT INTO download_jobs (symbol, interval, start_time, end_time,
                                       status, created_at, updated_at, log)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, '[]')
            """,
            (symbol, interval, start_time, end_time, now, now),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def get_job(job_id: int) -> dict | None:
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM download_jobs WHERE id=?", (job_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)


async def cancel_job(job_id: int) -> bool:
    async with get_db() as db:
        cursor = await db.execute(
            "UPDATE download_jobs SET status='cancelled', updated_at=? WHERE id=? AND status IN ('pending','running')",
            (_now_iso(), job_id),
        )
        await db.commit()
        return cursor.rowcount > 0


# Active tasks registry
_active_tasks: dict[int, asyncio.Task] = {}

# ---------------------------------------------------------------------------
# Automatic gap-fill for signal engine / live tracker
# ---------------------------------------------------------------------------

# Set of (symbol, interval) pairs currently being synced (avoids duplicate tasks)
_syncing: set[tuple[str, str]] = set()

# Cache: (symbol, interval) → end_ms up to which we confirmed data is complete.
# Avoids re-querying DB on every scanner cycle when nothing has changed.
_verified_ranges: dict[tuple[str, str], int] = {}


async def _sync_gaps_task(
    symbol: str, interval: str, start_ms: int, end_ms: int
) -> None:
    """
    Background coroutine: detect and fill gaps in [start_ms, end_ms) for
    symbol/interval without creating a download_jobs row.
    Removes (symbol, interval) from _syncing when done.
    """
    key = (symbol, interval)
    try:
        expected = _expected_open_times(start_ms, end_ms, interval)
        if not expected:
            return

        downloaded_at = _now_iso()
        BATCH_SIZE = 500
        step_ms = INTERVAL_MS[interval]

        async with get_db() as db:
            existing = await _get_existing_open_times(db, symbol, interval, start_ms, end_ms)
            gaps = sorted(set(expected) - existing)

        if not gaps:
            _verified_ranges[key] = end_ms
            return

        logger.info(
            "ensure_candles: syncing %d missing candles for %s %s",
            len(gaps), symbol, interval,
        )

        i = 0
        while i < len(gaps):
            batch_start = gaps[i]
            batch_end_idx = min(i + BATCH_SIZE, len(gaps))
            batch_end = gaps[batch_end_idx - 1] + step_ms

            try:
                raw_candles = await binance_client.get_klines(
                    symbol=symbol,
                    interval=interval,
                    start_time=batch_start,
                    end_time=batch_end - 1,
                    limit=BATCH_SIZE,
                )
            except Exception as exc:
                logger.warning(
                    "ensure_candles: klines fetch failed for %s %s batch %d: %s",
                    symbol, interval, i, exc,
                )
                return  # will retry on next scanner cycle

            candles = []
            for raw in raw_candles:
                c = parse_candle(raw, symbol, interval, downloaded_at)
                if validate_candle(c):
                    candles.append(c)

            async with get_db() as db:
                await _upsert_candles(db, candles)

            i = batch_end_idx

        # Final verification
        async with get_db() as db:
            final_existing = await _get_existing_open_times(db, symbol, interval, start_ms, end_ms)
        final_gaps = len(set(expected) - final_existing)
        if final_gaps == 0:
            _verified_ranges[key] = end_ms
            logger.info("ensure_candles: sync complete for %s %s", symbol, interval)
        else:
            logger.warning(
                "ensure_candles: %d gaps remain for %s %s after sync",
                final_gaps, symbol, interval,
            )

    except Exception as exc:
        logger.exception("ensure_candles: unexpected error for %s %s: %s", symbol, interval, exc)
    finally:
        _syncing.discard(key)


async def ensure_candles(
    symbol: str, interval: str, start_ms: int, end_ms: int
) -> bool:
    """
    Ensure [start_ms, end_ms) klines exist in DB for symbol/interval.

    Returns True if all required candles are already present (ready to scan).
    Returns False if a background sync was launched (caller should skip this cycle).

    Uses _verified_ranges to avoid re-querying DB when nothing has changed,
    and _syncing to prevent duplicate concurrent downloads for the same pair.
    """
    key = (symbol, interval)

    # Fast path: already verified up to (or beyond) end_ms
    if _verified_ranges.get(key, 0) >= end_ms:
        return True

    # If a sync is already running, just report not-ready
    if key in _syncing:
        return False

    # Check whether the last required candle (end_ms - step) is present
    step_ms = INTERVAL_MS.get(interval)
    if step_ms is None:
        raise ValueError(f"Unknown interval: {interval}")

    last_required = end_ms - step_ms  # open_time of the last candle we need

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM klines WHERE symbol=? AND interval=? AND open_time>=? AND open_time<?",
            (symbol, interval, start_ms, end_ms),
        )
        row = await cursor.fetchone()
        actual_count = row[0] if row else 0

        # Also check the critical last candle is there
        cursor2 = await db.execute(
            "SELECT 1 FROM klines WHERE symbol=? AND interval=? AND open_time=?",
            (symbol, interval, last_required),
        )
        has_last = await cursor2.fetchone() is not None

    expected_count = len(_expected_open_times(start_ms, end_ms, interval))

    if has_last and actual_count >= expected_count:
        # All candles present — update cache
        _verified_ranges[key] = end_ms
        return True

    # Launch background sync
    _syncing.add(key)
    asyncio.create_task(_sync_gaps_task(symbol, interval, start_ms, end_ms))
    logger.info(
        "ensure_candles: launched async sync for %s %s (%d/%d candles present)",
        symbol, interval, actual_count, expected_count,
    )
    return False


async def run_download_job(job_id: int) -> None:
    """Main download coroutine. Runs as a background asyncio Task."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT symbol, interval, start_time, end_time, status FROM download_jobs WHERE id=?",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return
        job = dict(row)

    if job["status"] == "cancelled":
        return

    symbol = job["symbol"]
    interval = job["interval"]
    start_ms = job["start_time"]
    end_ms = job["end_time"]

    async with get_db() as db:
        await _update_job(db, job_id, status="running", log_entry=f"Download started for {symbol} {interval}")

        try:
            # Step 1: Compute expected timestamps
            expected = _expected_open_times(start_ms, end_ms, interval)
            candles_expected = len(expected)
            await _update_job(db, job_id, candles_expected=candles_expected,
                              log_entry=f"Expected {candles_expected} candles")

            # Step 2: Find existing candles
            existing = await _get_existing_open_times(db, symbol, interval, start_ms, end_ms)

            # Step 3: Find gaps
            gaps = sorted(set(expected) - existing)
            await _update_job(db, job_id, gaps_found=len(gaps),
                              log_entry=f"Found {len(gaps)} missing candles")

            total_downloaded = len(existing)
            downloaded_at = _now_iso()

            # Step 4: Batch gaps into requests of 500
            BATCH_SIZE = 500
            step_ms = INTERVAL_MS[interval]
            i = 0
            while i < len(gaps):
                # Check for cancellation
                job_check = await get_job(job_id)
                if job_check and job_check["status"] == "cancelled":
                    logger.info("Job %d cancelled", job_id)
                    return

                batch_start = gaps[i]
                # Find how many consecutive gaps fit in 500
                batch_end_idx = min(i + BATCH_SIZE, len(gaps))
                batch_end = gaps[batch_end_idx - 1] + step_ms  # exclusive upper bound

                raw_candles = await binance_client.get_klines(
                    symbol=symbol,
                    interval=interval,
                    start_time=batch_start,
                    end_time=batch_end - 1,
                    limit=BATCH_SIZE,
                )

                candles = []
                for raw in raw_candles:
                    c = parse_candle(raw, symbol, interval, downloaded_at)
                    if validate_candle(c):
                        candles.append(c)
                    else:
                        logger.warning("Invalid candle skipped: %s", c)

                inserted = await _upsert_candles(db, candles)
                total_downloaded += inserted
                i = batch_end_idx

                progress = total_downloaded / max(candles_expected, 1) * 100
                await _update_job(
                    db, job_id,
                    candles_downloaded=total_downloaded,
                    progress_pct=min(progress, 100.0),
                    log_entry=f"Batch done: {total_downloaded}/{candles_expected} candles"
                )

            # Step 5: Re-scan for remaining gaps
            final_existing = await _get_existing_open_times(db, symbol, interval, start_ms, end_ms)
            final_gaps = set(expected) - final_existing
            await _update_job(
                db, job_id,
                status="completed",
                progress_pct=100.0,
                candles_downloaded=len(final_existing),
                gaps_found=len(final_gaps),
                log_entry=f"Download complete. Remaining gaps: {len(final_gaps)}",
            )

        except Exception as exc:
            logger.exception("Download job %d failed: %s", job_id, exc)
            await _update_job(db, job_id, status="failed", log_entry=f"Error: {exc}")


def start_download_job_task(job_id: int) -> asyncio.Task:
    """Schedule the download as a background asyncio Task."""
    task = asyncio.create_task(run_download_job(job_id))
    _active_tasks[job_id] = task

    def _cleanup(t: asyncio.Task) -> None:
        _active_tasks.pop(job_id, None)

    task.add_done_callback(_cleanup)
    return task
