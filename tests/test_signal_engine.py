"""Tests for signal_engine: scanner logic, dedup, stop_cross_pct calculation."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import pytest
from unittest.mock import patch, AsyncMock

import pandas as pd
import numpy as np


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    db_path = str(tmp_path / "test_signals.db")
    os.environ["DB_PATH"] = db_path
    import backend.database as dbmod
    dbmod.DB_PATH = __import__("pathlib").Path(db_path)
    yield


from backend.database import init_db, get_db
from backend.signal_engine import (
    _last_closed_candle_time,
    _create_signal_and_sim_trade,
    scan_config,
    WARMUP_CANDLES,
)
from backend.download_engine import INTERVAL_MS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candles_df(n: int, base_open_time: int = 0, step_ms: int = 3_600_000,
                     base_price: float = 100.0) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame."""
    rows = []
    price = base_price
    for i in range(n):
        o = price
        h = price + 2
        l = price - 2
        c = price + 1  # slight uptrend
        rows.append({
            "open_time": base_open_time + i * step_ms,
            "open": o, "high": h, "low": l, "close": c, "volume": 100.0,
        })
        price = c
    return pd.DataFrame(rows)


async def _setup_db():
    """Init the in-memory DB and return."""
    await init_db()


async def _insert_config(
    symbol="BTCUSDT", interval="1h", strategy="breakout",
    params=None, stop_cross_pct=0.02, portfolio=10000.0,
    leverage=1.0, cost_bps=10.0, active=1,
) -> dict:
    if params is None:
        params = {"N_entrada": 5, "M_salida": 3, "stop_pct": 0.02}
    params_json = json.dumps(params, sort_keys=True)
    now = "2025-01-01T00:00:00Z"
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO signal_configs
                (symbol, interval, strategy, params, stop_cross_pct,
                 portfolio, invested_amount, leverage, cost_bps,
                 polling_interval_s, active, last_processed_candle,
                 created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (symbol, interval, strategy, params_json, stop_cross_pct,
             portfolio, None, leverage, cost_bps, None, active, now, now),
        )
        await db.commit()
        config_id = cursor.lastrowid

        cursor2 = await db.execute(
            "SELECT * FROM signal_configs WHERE id = ?", (config_id,)
        )
        row = await cursor2.fetchone()
        cols = [d[0] for d in cursor2.description]
    return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLastClosedCandleTime:
    def test_1h_returns_past_hour(self):
        result = _last_closed_candle_time("1h")
        step_ms = INTERVAL_MS["1h"]
        # The result should be at least one step behind now
        import time
        now_ms = int(time.time() * 1000)
        assert result < now_ms
        assert result % step_ms == 0

    def test_1d_aligned(self):
        result = _last_closed_candle_time("1d")
        step_ms = INTERVAL_MS["1d"]
        assert result % step_ms == 0

    def test_unknown_interval_raises(self):
        with pytest.raises(ValueError):
            _last_closed_candle_time("99x")


class TestStopCrossPctCalculation:
    @pytest.mark.asyncio
    async def test_long_stop_trigger(self):
        await _setup_db()
        config = await _insert_config(stop_cross_pct=0.02)
        stop_price = 95.0  # base stop

        signal_id = await _create_signal_and_sim_trade(
            config=config,
            side="long",
            trigger_candle_time=1000000,
            stop_price=stop_price,
            stop_cross_pct=0.02,
        )
        assert signal_id is not None

        # Verify stop_trigger = stop_price * (1 - 0.02)
        expected_trigger = stop_price * (1.0 - 0.02)
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT stop_trigger FROM sim_trades WHERE signal_id = ?",
                (signal_id,),
            )
            row = await cursor.fetchone()
        assert row is not None
        assert abs(row[0] - expected_trigger) < 0.001

    @pytest.mark.asyncio
    async def test_short_stop_trigger(self):
        await _setup_db()
        config = await _insert_config(stop_cross_pct=0.03)
        stop_price = 105.0

        signal_id = await _create_signal_and_sim_trade(
            config=config,
            side="short",
            trigger_candle_time=2000000,
            stop_price=stop_price,
            stop_cross_pct=0.03,
        )
        assert signal_id is not None

        expected_trigger = stop_price * (1.0 + 0.03)
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT stop_trigger FROM sim_trades WHERE signal_id = ?",
                (signal_id,),
            )
            row = await cursor.fetchone()
        assert row is not None
        assert abs(row[0] - expected_trigger) < 0.001


class TestSignalDedup:
    @pytest.mark.asyncio
    async def test_duplicate_signal_returns_none(self):
        await _setup_db()
        config = await _insert_config()

        # First creation succeeds
        sid1 = await _create_signal_and_sim_trade(
            config=config, side="long",
            trigger_candle_time=5000000, stop_price=95.0, stop_cross_pct=0.02,
        )
        assert sid1 is not None

        # Second with same trigger_candle_time returns None (dedup)
        sid2 = await _create_signal_and_sim_trade(
            config=config, side="long",
            trigger_candle_time=5000000, stop_price=95.0, stop_cross_pct=0.02,
        )
        assert sid2 is None


class TestPortfolioModes:
    @pytest.mark.asyncio
    async def test_leverage_mode(self):
        """When leverage is given, invested_amount = portfolio * leverage."""
        await _setup_db()
        config = await _insert_config(portfolio=10000.0, leverage=2.0)

        signal_id = await _create_signal_and_sim_trade(
            config=config, side="long",
            trigger_candle_time=8000000, stop_price=95.0, stop_cross_pct=0.02,
        )
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT portfolio, invested_amount, leverage FROM sim_trades WHERE signal_id = ?",
                (signal_id,),
            )
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 10000.0  # portfolio
        assert row[1] == 20000.0  # invested_amount = 10000 * 2
        assert row[2] == 2.0      # leverage

    @pytest.mark.asyncio
    async def test_invested_amount_mode(self):
        """When invested_amount is given, leverage = invested / portfolio."""
        await _setup_db()
        # Need to insert config with invested_amount set
        now = "2025-01-01T00:00:00Z"
        params_json = json.dumps({"N_entrada": 5, "M_salida": 3, "stop_pct": 0.02}, sort_keys=True)
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO signal_configs
                    (symbol, interval, strategy, params, stop_cross_pct,
                     portfolio, invested_amount, leverage, cost_bps,
                     polling_interval_s, active, last_processed_candle,
                     created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                ("ETHUSDT", "1d", "breakout", params_json, 0.02,
                 10000.0, 5000.0, None, 10.0, None, 1, now, now),
            )
            await db.commit()
            config_id = cursor.lastrowid
            cursor2 = await db.execute(
                "SELECT * FROM signal_configs WHERE id = ?", (config_id,),
            )
            row = await cursor2.fetchone()
            cols = [d[0] for d in cursor2.description]
        config = dict(zip(cols, row))

        signal_id = await _create_signal_and_sim_trade(
            config=config, side="short",
            trigger_candle_time=9000000, stop_price=105.0, stop_cross_pct=0.02,
        )
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT portfolio, invested_amount, leverage FROM sim_trades WHERE signal_id = ?",
                (signal_id,),
            )
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 10000.0
        assert row[1] == 5000.0
        assert abs(row[2] - 0.5) < 0.001


class TestSimTradeStatus:
    @pytest.mark.asyncio
    async def test_sim_trade_starts_pending_entry(self):
        await _setup_db()
        config = await _insert_config()

        signal_id = await _create_signal_and_sim_trade(
            config=config, side="long",
            trigger_candle_time=11000000, stop_price=95.0, stop_cross_pct=0.02,
        )
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT status FROM sim_trades WHERE signal_id = ?",
                (signal_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == "pending_entry"

    @pytest.mark.asyncio
    async def test_signal_starts_pending(self):
        await _setup_db()
        config = await _insert_config()

        signal_id = await _create_signal_and_sim_trade(
            config=config, side="long",
            trigger_candle_time=12000000, stop_price=95.0, stop_cross_pct=0.02,
        )
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT status FROM signals WHERE id = ?",
                (signal_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == "pending"
