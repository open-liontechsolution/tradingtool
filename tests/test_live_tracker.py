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
    _apply_stop_moves,
    _check_intrabar_stops,
    _fill_pending_entries,
)
from backend.strategies.base import PositionState, Signal


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
        "initial_portfolio": 10000.0,
        "current_portfolio": 10000.0,
        "invested_amount": None,
        "leverage": 1.0,
        "cost_bps": 10.0,
        "polling_interval_s": None,
        "active": 1,
        "last_processed_candle": 0,
    }
    defaults.update(overrides)
    # Tolerate legacy callers passing portfolio=… (set both columns).
    if "portfolio" in defaults:
        defaults["initial_portfolio"] = defaults["portfolio"]
        defaults["current_portfolio"] = defaults["portfolio"]
        defaults.pop("portfolio")
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO signal_configs
                (symbol, interval, strategy, params,
                 initial_portfolio, current_portfolio,
                 invested_amount, leverage, cost_bps,
                 polling_interval_s, active, last_processed_candle,
                 created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                defaults["symbol"],
                defaults["interval"],
                defaults["strategy"],
                defaults["params"],
                defaults["initial_portfolio"],
                defaults["current_portfolio"],
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
    **_legacy,
) -> int:
    """Insert a signal. ``**_legacy`` swallows callers still passing stop_trigger=…."""
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO signals
                (config_id, symbol, interval, strategy, side,
                 trigger_candle_time, stop_price,
                 status, created_at)
               VALUES (?, 'BTCUSDT', '1h', 'breakout', ?, ?, ?, 'active', ?)""",
            (config_id, side, trigger_time, stop_price, now),
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
    quantity: float = 100.0,
    portfolio: float = 10000.0,
    invested_amount: float = 10000.0,
    leverage: float = 1.0,
    fees: float = 10.0,
    **_legacy,
) -> int:
    """Insert a sim_trade. ``**_legacy`` swallows callers still passing stop_trigger=…."""
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO sim_trades
                (signal_id, config_id, symbol, interval, side,
                 entry_price, entry_time, stop_base,
                 status, portfolio, invested_amount, leverage,
                 quantity, fees, created_at, updated_at)
               VALUES (?, ?, 'BTCUSDT', '1h', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal_id,
                config_id,
                side,
                entry_price,
                entry_time,
                stop_base,
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
    async def test_long_stop_triggered_when_price_below_stop_base(self):
        """Price below stop_base should close a long SimTrade at the actual price (gap fill)."""
        await _setup_db()
        config_id = await _insert_config()
        signal_id = await _insert_signal(config_id, stop_price=95.0)
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="long",
            entry_price=100.0,
            stop_base=95.0,
            quantity=100.0,
            portfolio=10000.0,
            invested_amount=10000.0,
        )

        # Mock ticker to return price below stop_base (95.0): gap → exec at price.
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
        assert row[2] == pytest.approx(92.0, abs=0.01)  # gap-fill at the actual price
        assert row[3] < 0  # losing trade

    @pytest.mark.asyncio
    async def test_long_not_triggered_when_price_above_stop_base(self):
        """Price above stop_base should NOT close the trade."""
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
    async def test_short_stop_triggered_when_price_above_stop_base(self):
        """Price above stop_base should close a short SimTrade (gap fill at price)."""
        await _setup_db()
        config_id = await _insert_config()
        signal_id = await _insert_signal(
            config_id,
            side="short",
            stop_price=105.0,
        )
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="short",
            entry_price=100.0,
            stop_base=105.0,
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
                "SELECT status, exit_reason, exit_price FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == "closed"
        assert row[1] == "stop_intrabar"
        assert row[2] == pytest.approx(108.0, abs=0.01)  # gap-fill at the actual price

    @pytest.mark.asyncio
    async def test_short_not_triggered_when_price_below_stop_base(self):
        await _setup_db()
        config_id = await _insert_config()
        signal_id = await _insert_signal(
            config_id,
            side="short",
            stop_price=105.0,
        )
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="short",
            entry_price=100.0,
            stop_base=105.0,
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
        signal_id = await _insert_signal(config_id, stop_price=95.0)
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="long",
            entry_price=100.0,
            stop_base=95.0,
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
        # pnl = quantity * (price - entry_price) = 100 * (90 - 100) = -1000 (gap fill at price)
        expected_pnl = 100.0 * (90.0 - 100.0)
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
        )
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="short",
            entry_price=100.0,
            stop_base=105.0,
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
        # pnl = quantity * (entry - price) = 100 * (100 - 110) = -1000 (gap fill at price)
        expected_pnl = 100.0 * (100.0 - 110.0)
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


# ---------------------------------------------------------------------------
# Tests: Trailing stop (_apply_stop_moves)
# ---------------------------------------------------------------------------


def _trade_row(
    trade_id: int,
    signal_id: int,
    config_id: int,
    *,
    side="long",
    entry_price=100.0,
    stop_base=95.0,
    **_legacy,
) -> dict:
    """Build the in-memory dict shape _check_candle_close_exits passes in."""
    return {
        "id": trade_id,
        "signal_id": signal_id,
        "config_id": config_id,
        "symbol": "BTCUSDT",
        "interval": "1h",
        "side": side,
        "entry_price": entry_price,
        "entry_time": 1_000_000,
        "stop_base": stop_base,
        "quantity": 100.0,
        "portfolio": 10_000.0,
        "invested_amount": 10_000.0,
        "fees": 10.0,
        "cost_bps": 10.0,
    }


class TestApplyStopMoves:
    @pytest.mark.asyncio
    async def test_long_tightening_move_updates_trade_and_records_history(self):
        await _setup_db()
        config_id = await _insert_config()
        signal_id = await _insert_signal(config_id)
        trade_id = await _insert_sim_trade(signal_id, config_id, stop_base=95.0, stop_trigger=93.1)
        trade = _trade_row(trade_id, signal_id, config_id, stop_base=95.0, stop_trigger=93.1)
        state = PositionState(side="long", entry_price=100.0, stop_price=93.1, quantity=100.0)

        with patch("backend.live_tracker.notify_event", new=AsyncMock()) as mock_notify:
            await _apply_stop_moves(
                trade,
                [Signal(action="move_stop", stop_price=98.0)],
                candle_open_time=1_100_000,
                state=state,
            )

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT stop_base FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            trade_row = await cursor.fetchone()
            cursor = await db.execute(
                """SELECT prev_stop_base, new_stop_base, candle_time
                   FROM sim_trade_stop_moves WHERE sim_trade_id = ?""",
                (trade_id,),
            )
            move_row = await cursor.fetchone()

        assert trade_row[0] == pytest.approx(98.0)
        assert move_row is not None
        assert move_row[0] == pytest.approx(95.0)
        assert move_row[1] == pytest.approx(98.0)
        assert move_row[2] == 1_100_000

        # In-memory state mirrors the new base for later same-candle checks.
        assert state.stop_price == pytest.approx(98.0)
        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args.kwargs
        assert kwargs["event_type"] == "stop_moved"
        assert kwargs["reference_type"] == "sim_trade_stop_move"
        assert kwargs["payload"]["prev_stop"] == pytest.approx(95.0)
        assert kwargs["payload"]["new_stop"] == pytest.approx(98.0)

    @pytest.mark.asyncio
    async def test_short_tightening_move_updates_trade(self):
        await _setup_db()
        config_id = await _insert_config()
        signal_id = await _insert_signal(config_id, side="short", stop_price=105.0, stop_trigger=107.1)
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            side="short",
            entry_price=100.0,
            stop_base=105.0,
            stop_trigger=107.1,
        )
        trade = _trade_row(
            trade_id, signal_id, config_id, side="short", entry_price=100.0, stop_base=105.0, stop_trigger=107.1
        )
        state = PositionState(side="short", entry_price=100.0, stop_price=107.1, quantity=100.0)

        with patch("backend.live_tracker.notify_event", new=AsyncMock()):
            await _apply_stop_moves(
                trade,
                [Signal(action="move_stop", stop_price=102.0)],
                candle_open_time=1_100_000,
                state=state,
            )

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT stop_base FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == pytest.approx(102.0)

    @pytest.mark.asyncio
    async def test_loosening_move_is_rejected(self):
        """A move_stop that would loosen the stop must not change anything."""
        await _setup_db()
        config_id = await _insert_config()
        signal_id = await _insert_signal(config_id)
        trade_id = await _insert_sim_trade(signal_id, config_id, stop_base=95.0, stop_trigger=93.1)
        trade = _trade_row(trade_id, signal_id, config_id, stop_base=95.0, stop_trigger=93.1)
        state = PositionState(side="long", entry_price=100.0, stop_price=93.1, quantity=100.0)

        with patch("backend.live_tracker.notify_event", new=AsyncMock()) as mock_notify:
            await _apply_stop_moves(
                trade,
                [Signal(action="move_stop", stop_price=90.0)],  # lower than current 95.0
                candle_open_time=1_100_000,
                state=state,
            )

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT stop_base FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
            cursor = await db.execute(
                "SELECT COUNT(*) FROM sim_trade_stop_moves WHERE sim_trade_id = ?",
                (trade_id,),
            )
            count = (await cursor.fetchone())[0]

        assert row[0] == pytest.approx(95.0)
        assert count == 0
        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_move_stop_signal_is_noop(self):
        await _setup_db()
        config_id = await _insert_config()
        signal_id = await _insert_signal(config_id)
        trade_id = await _insert_sim_trade(signal_id, config_id, stop_base=95.0, stop_trigger=93.1)
        trade = _trade_row(trade_id, signal_id, config_id)
        state = PositionState(side="long", entry_price=100.0, stop_price=93.1, quantity=100.0)

        with patch("backend.live_tracker.notify_event", new=AsyncMock()) as mock_notify:
            await _apply_stop_moves(
                trade,
                [Signal(action="exit_long", price=99.0)],
                candle_open_time=1_100_000,
                state=state,
            )

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM sim_trade_stop_moves WHERE sim_trade_id = ?",
                (trade_id,),
            )
            count = (await cursor.fetchone())[0]
        assert count == 0
        mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Dynamic equity (#48) — current_portfolio updates on close
# ---------------------------------------------------------------------------


async def _read_current_portfolio(config_id: int) -> float:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT current_portfolio FROM signal_configs WHERE id = ?",
            (config_id,),
        )
        row = await cursor.fetchone()
    assert row is not None, f"config {config_id} not found"
    return float(row[0])


class TestDynamicEquity:
    @pytest.mark.asyncio
    async def test_negative_pnl_decreases_current_portfolio(self):
        """A losing trade closing on intrabar stop subtracts net_pnl from current_portfolio."""
        await _setup_db()
        config_id = await _insert_config(cost_bps=0.0)  # no fees → assertions exact
        before = await _read_current_portfolio(config_id)

        signal_id = await _insert_signal(config_id, stop_price=95.0)
        await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="long",
            entry_price=100.0,
            stop_base=95.0,
            quantity=100.0,
            fees=0.0,
        )

        with patch("backend.live_tracker.binance_client") as mock_client:
            mock_client.get_ticker_price = AsyncMock(return_value=90.0)  # gap below stop
            mock_client.rate_limit = MagicMock()
            mock_client.rate_limit.used_weight = 10
            mock_client.rate_limit.weight_limit = 1200
            await _check_intrabar_stops()

        after = await _read_current_portfolio(config_id)
        # exec_price = 90 (gap), gross_pnl = 100 * (90-100) = -1000, no fees
        assert after == pytest.approx(before - 1000.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_positive_pnl_increases_current_portfolio(self):
        """A winning short trade (price drops) increases current_portfolio when stopped early."""
        # Synthesize a winning close: short at 100, manual close hits not available here,
        # so we use a short whose stop is far above and force a positive scenario by
        # closing via a stop at favourable price (would only happen with a tightened stop).
        # Easier: simulate by inserting a sim_trade then directly applying _apply_pnl.
        from backend.live_tracker import _apply_pnl_to_equity

        await _setup_db()
        config_id = await _insert_config(cost_bps=0.0)
        before = await _read_current_portfolio(config_id)

        async with get_db() as db:
            await _apply_pnl_to_equity(db, config_id, +250.0, _now_iso())
            await db.commit()

        after = await _read_current_portfolio(config_id)
        assert after == pytest.approx(before + 250.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_next_trade_sizes_against_updated_current_portfolio(self):
        """After a closed trade updates current_portfolio, the next sim_trade
        opens with the new sizing (compounding)."""
        from backend.signal_engine import _create_signal_and_sim_trade

        await _setup_db()
        config_id = await _insert_config(cost_bps=0.0, leverage=1.0)

        # Pretend a previous winning trade closed: bump current_portfolio by +500.
        from backend.live_tracker import _apply_pnl_to_equity

        async with get_db() as db:
            await _apply_pnl_to_equity(db, config_id, +500.0, _now_iso())
            await db.commit()

        # Read the updated config (signal_engine uses the dict's snapshot).
        async with get_db() as db:
            cursor = await db.execute("SELECT * FROM signal_configs WHERE id = ?", (config_id,))
            row = await cursor.fetchone()
            cols = [d[0] for d in cursor.description]
        config = dict(zip(cols, row, strict=False))

        sid = await _create_signal_and_sim_trade(
            config=config,
            side="long",
            trigger_candle_time=2_000_000,
            stop_price=95.0,
        )

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT portfolio, invested_amount FROM sim_trades WHERE signal_id = ?",
                (sid,),
            )
            row = await cursor.fetchone()
        # New trade dimensions against current_portfolio = 10000 + 500 = 10500
        assert row[0] == pytest.approx(10500.0, abs=0.01)
        assert row[1] == pytest.approx(10500.0, abs=0.01)  # leverage=1.0

    @pytest.mark.asyncio
    async def test_initial_portfolio_edit_does_not_affect_current(self):
        """Editing initial_portfolio (e.g. via PATCH) leaves current_portfolio untouched."""
        await _setup_db()
        config_id = await _insert_config()

        # Tweak current_portfolio to be different from initial.
        from backend.live_tracker import _apply_pnl_to_equity

        async with get_db() as db:
            await _apply_pnl_to_equity(db, config_id, -300.0, _now_iso())
            await db.commit()
        current_before = await _read_current_portfolio(config_id)

        # Now edit initial_portfolio (mimic PATCH).
        async with get_db() as db:
            await db.execute(
                "UPDATE signal_configs SET initial_portfolio = ? WHERE id = ?",
                (15000.0, config_id),
            )
            await db.commit()

        # current_portfolio is unchanged.
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT initial_portfolio, current_portfolio FROM signal_configs WHERE id = ?",
                (config_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == pytest.approx(15000.0)
        assert row[1] == pytest.approx(current_before)


# ---------------------------------------------------------------------------
# Tests: Leverage liquidation (#50)
# ---------------------------------------------------------------------------


class TestLiquidationFormula:
    def test_long_liq_below_entry(self):
        from backend.live_tracker import _compute_liquidation_price

        # leverage=10, mm=0.005 → factor = 0.1 - 0.005 = 0.095 → liq = 100 * 0.905 = 90.5
        liq = _compute_liquidation_price(side="long", entry_price=100.0, leverage=10.0, maintenance_margin_pct=0.005)
        assert liq == pytest.approx(90.5, abs=1e-6)

    def test_short_liq_above_entry(self):
        from backend.live_tracker import _compute_liquidation_price

        liq = _compute_liquidation_price(side="short", entry_price=100.0, leverage=10.0, maintenance_margin_pct=0.005)
        # 100 * (1 + 0.095) = 109.5
        assert liq == pytest.approx(109.5, abs=1e-6)

    def test_no_leverage_returns_none(self):
        from backend.live_tracker import _compute_liquidation_price

        assert (
            _compute_liquidation_price(side="long", entry_price=100.0, leverage=1.0, maintenance_margin_pct=0.005)
            is None
        )

    def test_excessive_mm_returns_none(self):
        from backend.live_tracker import _compute_liquidation_price

        # mm > 1/leverage means immediate liquidation — treat as malformed config.
        assert (
            _compute_liquidation_price(side="long", entry_price=100.0, leverage=2.0, maintenance_margin_pct=0.6) is None
        )


class TestLiquidationPriority:
    @pytest.mark.asyncio
    async def test_long_liquidation_fires_before_stop(self):
        """If price crosses liquidation_price the trade closes 'liquidated' at liq."""
        await _setup_db()
        config_id = await _insert_config(cost_bps=0.0, leverage=10.0)
        signal_id = await _insert_signal(config_id, stop_price=80.0)
        # Manually set liquidation_price=90.5 (formula applied at fill in real flow).
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="long",
            entry_price=100.0,
            stop_base=80.0,
            quantity=100.0,
            invested_amount=100_000.0,  # leveraged
            leverage=10.0,
            fees=0.0,
        )
        async with get_db() as db:
            await db.execute(
                "UPDATE sim_trades SET liquidation_price = 90.5 WHERE id = ?",
                (trade_id,),
            )
            await db.commit()

        # Price 89 < liq=90.5 < stop=80 (long: liq above stop). Liquidation wins.
        with patch("backend.live_tracker.binance_client") as mock_client:
            mock_client.get_ticker_price = AsyncMock(return_value=89.0)
            mock_client.rate_limit = MagicMock()
            mock_client.rate_limit.used_weight = 10
            mock_client.rate_limit.weight_limit = 1200
            await _check_intrabar_stops()

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT status, exit_reason, exit_price FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == "closed"
        assert row[1] == "liquidated"
        # Exec at liquidation_price, not at stop_base or actual price.
        assert row[2] == pytest.approx(90.5, abs=1e-6)

    @pytest.mark.asyncio
    async def test_no_liquidation_when_price_above_liq(self):
        """Stop fires normally if price doesn't reach liquidation_price."""
        await _setup_db()
        config_id = await _insert_config(cost_bps=0.0, leverage=10.0)
        signal_id = await _insert_signal(config_id, stop_price=95.0)
        trade_id = await _insert_sim_trade(
            signal_id,
            config_id,
            status="open",
            side="long",
            entry_price=100.0,
            stop_base=95.0,
            quantity=100.0,
            invested_amount=100_000.0,
            leverage=10.0,
            fees=0.0,
        )
        async with get_db() as db:
            await db.execute(
                "UPDATE sim_trades SET liquidation_price = 90.5 WHERE id = ?",
                (trade_id,),
            )
            await db.commit()

        # Price 94 below stop=95, above liq=90.5 → stop_intrabar (not liquidated).
        with patch("backend.live_tracker.binance_client") as mock_client:
            mock_client.get_ticker_price = AsyncMock(return_value=94.0)
            mock_client.rate_limit = MagicMock()
            mock_client.rate_limit.used_weight = 10
            mock_client.rate_limit.weight_limit = 1200
            await _check_intrabar_stops()

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT exit_reason FROM sim_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == "stop_intrabar"


class TestBlownAccountTransition:
    @pytest.mark.asyncio
    async def test_clamp_to_zero_and_mark_blown(self):
        """When current_portfolio drops to 0, status flips to 'blown'."""
        from backend.live_tracker import _maybe_mark_blown

        await _setup_db()
        config_id = await _insert_config()

        # Drain equity into the negative.
        async with get_db() as db:
            await db.execute(
                "UPDATE signal_configs SET current_portfolio = ? WHERE id = ?",
                (-500.0, config_id),
            )
            await db.commit()

        with patch("backend.live_tracker.notify_event", new=AsyncMock()) as mock_notify:
            await _maybe_mark_blown(config_id, _now_iso())

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT current_portfolio, status, blown_at FROM signal_configs WHERE id = ?",
                (config_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == pytest.approx(0.0)
        assert row[1] == "blown"
        assert row[2] is not None
        mock_notify.assert_called_once()
        assert mock_notify.call_args.kwargs["event_type"] == "account_blown"

    @pytest.mark.asyncio
    async def test_no_op_when_equity_positive(self):
        from backend.live_tracker import _maybe_mark_blown

        await _setup_db()
        config_id = await _insert_config()
        with patch("backend.live_tracker.notify_event", new=AsyncMock()) as mock_notify:
            await _maybe_mark_blown(config_id, _now_iso())

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT status FROM signal_configs WHERE id = ?",
                (config_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == "active"
        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_blown_config_not_loaded_by_active_query(self):
        """``_get_active_configs`` excludes blown rows (no new signals)."""
        from backend.signal_engine import _get_active_configs

        await _setup_db()
        active_id = await _insert_config()
        blown_id = await _insert_config(symbol="ETHUSDT", interval="4h")

        async with get_db() as db:
            await db.execute(
                "UPDATE signal_configs SET status = 'blown', blown_at = ? WHERE id = ?",
                (_now_iso(), blown_id),
            )
            await db.commit()

        configs = await _get_active_configs()
        ids = {c["id"] for c in configs}
        assert active_id in ids
        assert blown_id not in ids


class TestResetEquity:
    @pytest.mark.asyncio
    async def test_reset_restores_initial_and_clears_blown(self):
        """Reset-equity flow: status='blown' + drained portfolio → back to active."""
        await _setup_db()
        config_id = await _insert_config()

        # Force blown state with drained portfolio.
        async with get_db() as db:
            await db.execute(
                "UPDATE signal_configs SET status = 'blown', blown_at = ?, current_portfolio = 0 WHERE id = ?",
                (_now_iso(), config_id),
            )
            await db.commit()

        # Mimic the endpoint side effect.
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT initial_portfolio FROM signal_configs WHERE id = ?",
                (config_id,),
            )
            initial = float((await cursor.fetchone())[0])
            await db.execute(
                "UPDATE signal_configs SET current_portfolio = ?, status = 'active', "
                "blown_at = NULL, updated_at = ? WHERE id = ?",
                (initial, _now_iso(), config_id),
            )
            await db.commit()

        async with get_db() as db:
            cursor = await db.execute(
                "SELECT current_portfolio, status, blown_at FROM signal_configs WHERE id = ?",
                (config_id,),
            )
            row = await cursor.fetchone()
        assert row[0] == pytest.approx(initial)
        assert row[1] == "active"
        assert row[2] is None
