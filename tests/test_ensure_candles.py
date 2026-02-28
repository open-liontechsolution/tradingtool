"""Tests for ensure_candles() in download_engine and its integration with scan_config."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock, patch

import pytest

from backend.database import get_db, init_db
from backend.download_engine import (
    INTERVAL_MS,
    _syncing,
    _verified_ranges,
    ensure_candles,
)


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    db_path = str(tmp_path / "test_ensure.db")
    os.environ["DB_PATH"] = db_path
    import backend.database as dbmod

    dbmod.DB_PATH = __import__("pathlib").Path(db_path)
    # Reset module-level caches between tests
    import backend.download_engine as de

    de._syncing.clear()
    de._verified_ranges.clear()
    yield
    de._syncing.clear()
    de._verified_ranges.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_candles(symbol: str, interval: str, open_times: list[int]) -> None:
    """Insert minimal kline rows for the given open_times."""
    step_ms = INTERVAL_MS[interval]
    now_iso = "2025-01-01T00:00:00Z"
    rows = [
        (symbol, interval, ot, "100", "101", "99", "100.5", "10", ot + step_ms - 1, "1000", 10, "5", "500", now_iso)
        for ot in open_times
    ]
    async with get_db() as db:
        await db.executemany(
            """INSERT OR REPLACE INTO klines
                (symbol, interval, open_time, open, high, low, close, volume,
                 close_time, quote_asset_volume, number_of_trades,
                 taker_buy_base_vol, taker_buy_quote_vol, downloaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Tests: ensure_candles returns True when data is complete
# ---------------------------------------------------------------------------


class TestEnsureCandlesDataPresent:
    @pytest.mark.asyncio
    async def test_returns_true_when_all_candles_present(self):
        await init_db()
        step_ms = INTERVAL_MS["1h"]
        start_ms = 1_000 * step_ms
        end_ms = start_ms + 5 * step_ms

        open_times = list(range(start_ms, end_ms, step_ms))
        await _insert_candles("BTCUSDT", "1h", open_times)

        result = await ensure_candles("BTCUSDT", "1h", start_ms, end_ms)
        assert result is True

    @pytest.mark.asyncio
    async def test_updates_verified_ranges_cache_on_success(self):
        await init_db()
        step_ms = INTERVAL_MS["1h"]
        start_ms = 2_000 * step_ms
        end_ms = start_ms + 3 * step_ms

        open_times = list(range(start_ms, end_ms, step_ms))
        await _insert_candles("BTCUSDT", "1h", open_times)

        await ensure_candles("BTCUSDT", "1h", start_ms, end_ms)
        assert _verified_ranges.get(("BTCUSDT", "1h"), 0) >= end_ms

    @pytest.mark.asyncio
    async def test_fast_path_via_cache(self):
        """If _verified_ranges already covers end_ms, no DB query should run."""
        await init_db()
        step_ms = INTERVAL_MS["1h"]
        start_ms = 3_000 * step_ms
        end_ms = start_ms + 5 * step_ms

        # Pre-populate cache (no candles in DB)
        _verified_ranges[("ETHUSDT", "1h")] = end_ms + step_ms

        result = await ensure_candles("ETHUSDT", "1h", start_ms, end_ms)
        assert result is True  # cache hit, no DB lookup

    @pytest.mark.asyncio
    async def test_missing_last_candle_triggers_sync(self):
        """If the last required candle is absent, ensure_candles returns False."""
        await init_db()
        step_ms = INTERVAL_MS["1h"]
        start_ms = 5_000 * step_ms
        end_ms = start_ms + 4 * step_ms

        # Insert all EXCEPT the last candle
        open_times = list(range(start_ms, end_ms - step_ms, step_ms))
        await _insert_candles("BTCUSDT", "1h", open_times)

        with patch("backend.download_engine.asyncio.create_task") as mock_task:
            result = await ensure_candles("BTCUSDT", "1h", start_ms, end_ms)

        assert result is False
        mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_gap_in_middle_triggers_sync(self):
        """A gap in the middle should also trigger a sync."""
        await init_db()
        step_ms = INTERVAL_MS["1h"]
        start_ms = 6_000 * step_ms
        end_ms = start_ms + 5 * step_ms

        open_times = list(range(start_ms, end_ms, step_ms))
        open_times_with_gap = [t for t in open_times if t != open_times[2]]  # remove middle
        await _insert_candles("BTCUSDT", "1h", open_times_with_gap)

        with patch("backend.download_engine.asyncio.create_task") as mock_task:
            result = await ensure_candles("BTCUSDT", "1h", start_ms, end_ms)

        assert result is False
        mock_task.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: deduplication — no duplicate sync tasks
# ---------------------------------------------------------------------------


class TestEnsureCandlesDedup:
    @pytest.mark.asyncio
    async def test_no_duplicate_task_if_already_syncing(self):
        """If (symbol, interval) is in _syncing, should return False without new task."""
        await init_db()
        step_ms = INTERVAL_MS["1h"]
        start_ms = 8_000 * step_ms
        end_ms = start_ms + 3 * step_ms

        _syncing.add(("BTCUSDT", "1h"))

        with patch("backend.download_engine.asyncio.create_task") as mock_task:
            result = await ensure_candles("BTCUSDT", "1h", start_ms, end_ms)

        assert result is False
        mock_task.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: scan_config skips when ensure_candles returns False
# ---------------------------------------------------------------------------


class TestScanConfigEnsureCandlesIntegration:
    @pytest.mark.asyncio
    async def test_scan_config_skips_when_data_not_ready(self, tmp_path):
        """scan_config should return early without calling load_candles_df when ensure_candles=False."""
        await init_db()

        config = {
            "id": 1,
            "symbol": "BTCUSDT",
            "interval": "1h",
            "strategy": "breakout",
            "params": json.dumps({"N_entrada": 5, "M_salida": 3, "stop_pct": 0.02}),
            "stop_cross_pct": 0.02,
            "portfolio": 10000.0,
            "invested_amount": None,
            "leverage": 1.0,
            "cost_bps": 10.0,
            "last_processed_candle": 0,
        }

        with (
            patch("backend.signal_engine.ensure_candles", new=AsyncMock(return_value=False)),
            patch("backend.signal_engine.load_candles_df") as mock_load,
        ):
            from backend.signal_engine import scan_config

            await scan_config(config)
            mock_load.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_config_proceeds_when_data_ready(self):
        """scan_config should call load_candles_df when ensure_candles returns True."""
        await init_db()

        import pandas as pd

        step_ms = INTERVAL_MS["1h"]
        now_ms = int(time.time() * 1000)
        last_closed = ((now_ms // step_ms) * step_ms) - step_ms

        # Build a minimal dataframe with last_closed as the last row
        rows = []
        for i in range(10):
            ot = last_closed - (9 - i) * step_ms
            rows.append(
                {
                    "open_time": ot,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 10.0,
                }
            )
        df = pd.DataFrame(rows)

        config = {
            "id": 2,
            "symbol": "BTCUSDT",
            "interval": "1h",
            "strategy": "breakout",
            "params": json.dumps({"N_entrada": 5, "M_salida": 3, "stop_pct": 0.02}),
            "stop_cross_pct": 0.02,
            "portfolio": 10000.0,
            "invested_amount": None,
            "leverage": 1.0,
            "cost_bps": 10.0,
            "last_processed_candle": 0,
        }

        with (
            patch("backend.signal_engine.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.signal_engine.load_candles_df", new=AsyncMock(return_value=df)),
        ):
            from backend.signal_engine import scan_config

            # Should not raise; will try to init strategy with tiny df but won't crash
            await scan_config(config)
