"""Tests for live_tracker: stop logic, entry fill, candle-close exits."""

from __future__ import annotations

import json
import os
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.database import get_db, init_db
from backend.download_engine import INTERVAL_MS
from backend.live_tracker import (
    _check_intrabar_stops,
    _fill_pending_entries,
)


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    db_path = str(tmp_path / "test_tracker.db")
    os.environ["DB_PATH"] = db_path
    import backend.database as dbmod

    dbmod.DB_PATH = __import__("pathlib").Path(db_path)
    yield


def _now_iso():
    from datetime import datetime

    return datetime.now(UTC).isoformat()


async def _setup_db():
    await init_db()


async def _insert_config(**overrides) -> int:
    defaults = {
        "symbol": "BTCUSDT",
        "interval": "1h",
        "strategy": "breakout",
        "params": json.dumps({"N_entrada": 5, "M_salida": 3, "stop_pct": 0.02}, sort_keys=True),
        "stop_cross_pct": 0.02,
        "portfolio": 10000.0,
        "invested_amount": None,
        "leverage": 1.0,
        "cost_bps": 10.0,
        "polling_interval_s": None,
        "active": 1,
        "last_processed_candle": 0,
    }
    defaults.update(overrides)
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO signal_configs
                (symbol, interval, strategy, params, stop_cross_pct,
                 portfolio, invested_amount, leverage, cost_bps,
                 polling_interval_s, active, last_processed_candle,
                 created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                defaults["symbol"],
                defaults["interval"],
                defaults["strategy"],
                defaults["params"],
                defaults["stop_cross_pct"],
                defaults["portfolio"],
                defaults["invested_amount"],
                defaults["leverage"],
                defaults["cost_bps"],
                defaults["polling_interval_s"],
                defaults["active"],
                defaults["last_processed_candle"],
                now,
                now,
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def _insert_signal(
    config_id: int,
    trigger_time: int = 1000000,
    side: str = "long",
    stop_price: float = 95.0,
    stop_trigger: float = 93.1,
) -> int:
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO signals
                (config_id, symbol, interval, strategy, side,
                 trigger_candle_time, stop_price, stop_trigger_price,
                 status, created_at)
               VALUES (?, 'BTCUSDT', '1h', 'breakout', ?, ?, ?, ?, 'active', ?)""",
            (config_id, side, trigger_time, stop_price, stop_trigger, now),
        )
        await db.commit()
        return cursor.lastrowid


async def _insert_sim_trade(
    signal_id: int,
    config_id: int,
    status: str = "open",
    side: str = "long",
    entry_price: float = 100.0,
    entry_time: int = 1000000,
    stop_base: float = 95.0,
    stop_trigger: float = 93.1,
    quantity: float = 100.0,
    portfolio: float = 10000.0,
    invested_amount: float = 10000.0,
    leverage: float = 1.0,
    fees: float = 10.0,
) -> int:
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO sim_trades
                (signal_id, config_id, symbol, interval, side,
                 entry_price, entry_time, stop_base, stop_trigger,
                 status, portfolio, invested_amount, leverage,
                 quantity, fees, created_at, updated_at)
               VALUES (?, ?, 'BTCUSDT', '1h', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal_id,
                config_id,
                side,
                entry_price,
                entry_time,
                stop_base,
                stop_trigger,
                status,
                portfolio,
                invested_amount,
                leverage,
                quantity,
                fees,
                now,
                now,
            ),
        )
        await db.commit()
        return cursor.lastrowid


# ---------------------------------------------------------------------------
# Tests: Intrabar stop
# ---------------------------------------------------------------------------


class TestIntrabarStop:
    @pytest.mark.asyncio
    async def test_long_stop_triggered_when_price_below_trigger(self):
        """Price below stop_trigger should close a long SimTrade."""
        await _setup_db()
        config_id = await _insert_config()
        signal_id = await _insert_signal(config_id, stop_price=95.0, stop_trigger=93.1)
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="long",
            entry_price=100.0,
            stop_base=95.0,
            stop_trigger=93.1,
            quantity=100.0,
            portfolio=10000.0,
            invested_amount=10000.0,
        )

        # Mock ticker to return price below stop_trigger (93.1)
        with patch("backend.live_tracker.binance_client") as mock_client:
            mock_client.get_ticker_price = AsyncMock(return_value=92.0)
            mock_client.rate_limit = MagicMock()
            mock_client.rate_limit.used_weight = 10
            mock_client.rate_limit.weight_limit = 1200
            await _check_intrabar_stops()

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT status, exit_reason, exit_price, pnl FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()

        assert row[0] == "closed"
        assert row[1] == "stop_intrabar"
        assert row[2] == pytest.approx(93.1, abs=0.01)  # exit at stop_trigger
        assert row[3] < 0  # losing trade

    @pytest.mark.asyncio
    async def test_long_not_triggered_when_price_above_trigger(self):
        """Price above stop_trigger should NOT close the trade."""
        await _setup_db()
        config_id = await _insert_config()
        signal_id = await _insert_signal(config_id, stop_price=95.0, stop_trigger=93.1)
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="long",
            entry_price=100.0,
            stop_base=95.0,
            stop_trigger=93.1,
            quantity=100.0,
        )

        with patch("backend.live_tracker.binance_client") as mock_client:
            mock_client.get_ticker_price = AsyncMock(return_value=96.0)
            mock_client.rate_limit = MagicMock()
            mock_client.rate_limit.used_weight = 10
            mock_client.rate_limit.weight_limit = 1200
            await _check_intrabar_stops()

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT status FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == "open"

    @pytest.mark.asyncio
    async def test_short_stop_triggered_when_price_above_trigger(self):
        """Price above stop_trigger should close a short SimTrade."""
        await _setup_db()
        config_id = await _insert_config()
        # Short: stop_base=105, trigger=105*(1+0.02)=107.1
        signal_id = await _insert_signal(
            config_id,
            side="short",
            stop_price=105.0,
            stop_trigger=107.1,
        )
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="short",
            entry_price=100.0,
            stop_base=105.0,
            stop_trigger=107.1,
            quantity=100.0,
        )

        with patch("backend.live_tracker.binance_client") as mock_client:
            mock_client.get_ticker_price = AsyncMock(return_value=108.0)
            mock_client.rate_limit = MagicMock()
            mock_client.rate_limit.used_weight = 10
            mock_client.rate_limit.weight_limit = 1200
            await _check_intrabar_stops()

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT status, exit_reason FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == "closed"
        assert row[1] == "stop_intrabar"

    @pytest.mark.asyncio
    async def test_short_not_triggered_when_price_below_trigger(self):
        await _setup_db()
        config_id = await _insert_config()
        signal_id = await _insert_signal(
            config_id,
            side="short",
            stop_price=105.0,
            stop_trigger=107.1,
        )
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="short",
            entry_price=100.0,
            stop_base=105.0,
            stop_trigger=107.1,
            quantity=100.0,
        )

        with patch("backend.live_tracker.binance_client") as mock_client:
            mock_client.get_ticker_price = AsyncMock(return_value=104.0)
            mock_client.rate_limit = MagicMock()
            mock_client.rate_limit.used_weight = 10
            mock_client.rate_limit.weight_limit = 1200
            await _check_intrabar_stops()

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT status FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == "open"


class TestNotificationDedup:
    @pytest.mark.asyncio
    async def test_stop_creates_notification(self):
        """Stopping a trade should create a notification_log entry."""
        await _setup_db()
        config_id = await _insert_config()
        signal_id = await _insert_signal(config_id, stop_trigger=93.1)
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="long",
            entry_price=100.0,
            stop_trigger=93.1,
            quantity=100.0,
        )

        with patch("backend.live_tracker.binance_client") as mock_client:
            mock_client.get_ticker_price = AsyncMock(return_value=90.0)
            mock_client.rate_limit = MagicMock()
            mock_client.rate_limit.used_weight = 10
            mock_client.rate_limit.weight_limit = 1200
            await _check_intrabar_stops()

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT event_type, reference_type, reference_id FROM notification_log WHERE reference_id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "stop_hit"
        assert row[1] == "sim_trade"


class TestPnlCalculation:
    @pytest.mark.asyncio
    async def test_long_stop_pnl_negative(self):
        """A long stopped below entry should have negative PnL."""
        await _setup_db()
        config_id = await _insert_config(cost_bps=0.0)  # no fees for simplicity
        signal_id = await _insert_signal(config_id, stop_trigger=93.1)
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="long",
            entry_price=100.0,
            stop_trigger=93.1,
            quantity=100.0,
            fees=0.0,
        )

        with patch("backend.live_tracker.binance_client") as mock_client:
            mock_client.get_ticker_price = AsyncMock(return_value=90.0)
            mock_client.rate_limit = MagicMock()
            mock_client.rate_limit.used_weight = 10
            mock_client.rate_limit.weight_limit = 1200
            await _check_intrabar_stops()

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT pnl, pnl_pct FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        # pnl = quantity * (stop_trigger - entry_price) = 100 * (93.1 - 100) = -690
        expected_pnl = 100.0 * (93.1 - 100.0)
        assert row[0] == pytest.approx(expected_pnl, abs=1.0)
        assert row[1] < 0

    @pytest.mark.asyncio
    async def test_short_stop_pnl_negative(self):
        await _setup_db()
        config_id = await _insert_config(cost_bps=0.0)
        signal_id = await _insert_signal(
            config_id,
            side="short",
            stop_price=105.0,
            stop_trigger=107.1,
        )
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="short",
            entry_price=100.0,
            stop_base=105.0,
            stop_trigger=107.1,
            quantity=100.0,
            fees=0.0,
        )

        with patch("backend.live_tracker.binance_client") as mock_client:
            mock_client.get_ticker_price = AsyncMock(return_value=110.0)
            mock_client.rate_limit = MagicMock()
            mock_client.rate_limit.used_weight = 10
            mock_client.rate_limit.weight_limit = 1200
            await _check_intrabar_stops()

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT pnl FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        # pnl = quantity * (entry - stop_trigger) = 100 * (100 - 107.1) = -710
        expected_pnl = 100.0 * (100.0 - 107.1)
        assert row[0] == pytest.approx(expected_pnl, abs=1.0)


class TestPendingEntryFill:
    @pytest.mark.asyncio
    async def test_fill_from_db_candle(self):
        """Pending entry should fill when next candle exists in DB."""
        await _setup_db()
        config_id = await _insert_config()
        step_ms = INTERVAL_MS["1h"]
        trigger_time = 1000 * step_ms  # some candle open_time

        signal_id = await _insert_signal(config_id, trigger_time=trigger_time)
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="pending_entry",
            entry_price=None,
            entry_time=None,
            stop_base=95.0,
            stop_trigger=93.1,
            quantity=None,
        )

        # Insert the "next candle" into klines
        next_open = trigger_time + step_ms
        now_iso = _now_iso()
        async with get_db() as db:
            await db.execute(
                """INSERT INTO klines
                    (symbol, interval, open_time, open, high, low, close, volume,
                     close_time, quote_asset_volume, number_of_trades,
                     taker_buy_base_vol, taker_buy_quote_vol, downloaded_at)
                   VALUES ('BTCUSDT', '1h', ?, '50000.0', '50500.0', '49500.0', '50200.0', '100',
                           ?, '5000000', 1000, '50', '2500000', ?)""",
                (next_open, next_open + step_ms - 1, now_iso),
            )
            await db.commit()

        await _fill_pending_entries()

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT status, entry_price, quantity FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == "open"
        assert row[1] == pytest.approx(50000.0, abs=0.01)
        assert row[2] is not None and row[2] > 0
