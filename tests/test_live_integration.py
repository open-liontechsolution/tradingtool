"""Integration tests for the live trading mode (signal_engine + live_tracker).

Tests the full signal lifecycle using synthetic candles and a controlled fake clock
instead of real wall-clock time.  The approach patches _now_ms() in each module,
pre-loads candle data into a temp SQLite DB, and calls the internal functions
(scan_config, _fill_pending_entries, _check_intrabar_stops, _check_candle_close_exits)
directly — the same way backtest_engine iterates over candles without asyncio.sleep.

"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend.database import get_db, init_db
from backend.download_engine import INTERVAL_MS
from backend.live_tracker import (
    _check_candle_close_exits,
    _check_intrabar_stops,
    _fill_pending_entries,
)
from backend.signal_engine import scan_config

# ---------------------------------------------------------------------------
# Constants — candle times are multiples of STEP_MS to ensure proper alignment
# ---------------------------------------------------------------------------

STEP_MS = INTERVAL_MS["1h"]
BASE_TIME = (1_700_000_000_000 // STEP_MS) * STEP_MS  # aligned to 1h boundary


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    db_path = str(tmp_path / "test_live_integration.db")
    os.environ["DB_PATH"] = db_path
    import backend.database as dbmod

    dbmod.DB_PATH = __import__("pathlib").Path(db_path)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _setup_db() -> None:
    await init_db()


async def _insert_config(**overrides) -> dict:
    """Insert a signal_config with breakout defaults and return the full row dict."""
    defaults = {
        "symbol": "BTCUSDT",
        "interval": "1h",
        "strategy": "breakout",
        "params": json.dumps(
            {"N_entrada": 5, "M_salida": 3, "stop_pct": 0.02, "salida_por_ruptura": True},
            sort_keys=True,
        ),
        "portfolio": 10000.0,
        "invested_amount": None,
        "leverage": 1.0,
        "cost_bps": 0.0,  # no fees simplifies PnL assertions
        "polling_interval_s": None,
        "active": 1,
        "last_processed_candle": 0,
    }
    defaults.update(overrides)
    # Tolerate legacy callers that still pass stop_cross_pct=...
    defaults.pop("stop_cross_pct", None)
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO signal_configs
                (symbol, interval, strategy, params,
                 portfolio, invested_amount, leverage, cost_bps,
                 polling_interval_s, active, last_processed_candle,
                 created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                defaults["symbol"],
                defaults["interval"],
                defaults["strategy"],
                defaults["params"],
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
        config_id = cursor.lastrowid
        cursor2 = await db.execute("SELECT * FROM signal_configs WHERE id = ?", (config_id,))
        row = await cursor2.fetchone()
        cols = [d[0] for d in cursor2.description]
    return dict(zip(cols, row, strict=False))


def _make_flat_candles(n: int, base_time: int = BASE_TIME, price: float = 100.0) -> list[dict]:
    """Generate n flat 1h candles starting at base_time (all times are STEP_MS multiples)."""
    return [
        {
            "open_time": base_time + i * STEP_MS,
            "open": price,
            "high": price + 2,  # high = 102
            "low": price - 2,  # low  = 98
            "close": price,
            "volume": 1000.0,
            "close_time": base_time + i * STEP_MS + STEP_MS - 1,
        }
        for i in range(n)
    ]


def _candles_to_df(candles: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(candles)


async def _insert_kline(candle: dict, symbol: str = "BTCUSDT", interval: str = "1h") -> None:
    """Insert a single candle into the klines table (values stored as TEXT like Binance)."""
    now = _now_iso()
    async with get_db() as db:
        await db.execute(
            """INSERT OR REPLACE INTO klines
                (symbol, interval, open_time, open, high, low, close, volume,
                 close_time, quote_asset_volume, number_of_trades,
                 taker_buy_base_vol, taker_buy_quote_vol, downloaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol,
                interval,
                candle["open_time"],
                str(candle["open"]),
                str(candle["high"]),
                str(candle["low"]),
                str(candle["close"]),
                str(candle["volume"]),
                candle.get("close_time", candle["open_time"] + STEP_MS - 1),
                "0",
                0,
                "0",
                "0",
                now,
            ),
        )
        await db.commit()


def _mock_binance(ticker_price: float = 110.0) -> MagicMock:
    mock = MagicMock()
    mock.get_ticker_price = AsyncMock(return_value=ticker_price)
    mock.rate_limit = MagicMock()
    mock.rate_limit.used_weight = 10
    mock.rate_limit.weight_limit = 1200
    return mock


async def _get_sim_trades(status: str | None = None) -> list[dict]:
    async with get_db() as db:
        if status:
            cursor = await db.execute("SELECT * FROM sim_trades WHERE status = ?", (status,))
        else:
            cursor = await db.execute("SELECT * FROM sim_trades")
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r, strict=False)) for r in rows]


async def _get_signals(status: str | None = None) -> list[dict]:
    async with get_db() as db:
        if status:
            cursor = await db.execute("SELECT * FROM signals WHERE status = ?", (status,))
        else:
            cursor = await db.execute("SELECT * FROM signals")
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r, strict=False)) for r in rows]


def _make_breakout_df() -> tuple[pd.DataFrame, int, float]:
    """
    Build a DataFrame for a clear long breakout scenario.

    Returns (df, breakout_time, stop_base) where:
      - 30 flat candles at price=100 (high=102, low=98)
      - 1 breakout candle: close=125 > max_prev=102 → entry_long
      - stop_base = min_prev * (1 - stop_pct) = 98 * 0.98 = 96.04
    """
    flat = _make_flat_candles(30)
    breakout_time = flat[-1]["open_time"] + STEP_MS  # index 30
    breakout_candle = {
        "open_time": breakout_time,
        "open": 100.0,
        "high": 130.0,
        "low": 99.0,
        "close": 125.0,
        "volume": 2000.0,
        "close_time": breakout_time + STEP_MS - 1,
    }
    df = _candles_to_df(flat + [breakout_candle])
    stop_base = 98.0 * (1.0 - 0.02)  # = 96.04
    return df, breakout_time, stop_base


# ---------------------------------------------------------------------------
# Tests: signal detection (scan_config)
# ---------------------------------------------------------------------------


class TestSignalDetection:
    @pytest.mark.asyncio
    async def test_breakout_long_creates_signal_and_pending_trade(self):
        """scan_config detects a long breakout and creates signal + sim_trade."""
        await _setup_db()
        config = await _insert_config()
        df, breakout_time, stop_base = _make_breakout_df()

        # Fake clock: we are in the candle after the breakout (breakout is last closed)
        fake_now = breakout_time + STEP_MS + 1

        with (
            patch("backend.signal_engine._now_ms", return_value=fake_now),
            patch("backend.signal_engine.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.signal_engine.load_candles_df", new=AsyncMock(return_value=df)),
        ):
            await scan_config(config)

        signals = await _get_signals()
        trades = await _get_sim_trades()

        assert len(signals) == 1
        assert signals[0]["side"] == "long"
        assert signals[0]["status"] == "pending"
        assert signals[0]["trigger_candle_time"] == breakout_time
        assert signals[0]["stop_price"] == pytest.approx(stop_base, abs=0.01)

        assert len(trades) == 1
        assert trades[0]["status"] == "pending_entry"
        assert trades[0]["entry_price"] is None
        assert trades[0]["stop_base"] == pytest.approx(stop_base, abs=0.01)

    @pytest.mark.asyncio
    async def test_last_processed_candle_updated_after_scan(self):
        """scan_config updates last_processed_candle even when no signal fires."""
        await _setup_db()
        config = await _insert_config()

        # Flat candles only (no breakout) — strategy produces no entry signal
        flat = _make_flat_candles(30)
        last_candle_time = flat[-1]["open_time"]
        df = _candles_to_df(flat)
        fake_now = last_candle_time + STEP_MS + 1

        with (
            patch("backend.signal_engine._now_ms", return_value=fake_now),
            patch("backend.signal_engine.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.signal_engine.load_candles_df", new=AsyncMock(return_value=df)),
        ):
            await scan_config(config)

        async with get_db() as db:
            cursor = await db.execute("SELECT last_processed_candle FROM signal_configs WHERE id = ?", (config["id"],))
            row = await cursor.fetchone()
        assert row[0] == last_candle_time

    @pytest.mark.asyncio
    async def test_already_processed_candle_skips_scan(self):
        """If last_processed_candle == last_closed, scan returns immediately."""
        await _setup_db()
        config = await _insert_config()
        df, breakout_time, _ = _make_breakout_df()
        fake_now = breakout_time + STEP_MS + 1

        # Mark the breakout candle as already processed
        async with get_db() as db:
            await db.execute(
                "UPDATE signal_configs SET last_processed_candle = ? WHERE id = ?",
                (breakout_time, config["id"]),
            )
            await db.commit()
        config["last_processed_candle"] = breakout_time

        mock_load = AsyncMock()
        with (
            patch("backend.signal_engine._now_ms", return_value=fake_now),
            patch("backend.signal_engine.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.signal_engine.load_candles_df", mock_load),
        ):
            await scan_config(config)

        mock_load.assert_not_called()
        assert len(await _get_signals()) == 0

    @pytest.mark.asyncio
    async def test_skip_when_ensure_candles_not_ready(self):
        """If ensure_candles returns False, scan is skipped and last_processed stays 0."""
        await _setup_db()
        config = await _insert_config()
        df, breakout_time, _ = _make_breakout_df()
        fake_now = breakout_time + STEP_MS + 1

        mock_load = AsyncMock()
        with (
            patch("backend.signal_engine._now_ms", return_value=fake_now),
            patch("backend.signal_engine.ensure_candles", new=AsyncMock(return_value=False)),
            patch("backend.signal_engine.load_candles_df", mock_load),
        ):
            await scan_config(config)

        mock_load.assert_not_called()
        assert len(await _get_signals()) == 0

        async with get_db() as db:
            cursor = await db.execute("SELECT last_processed_candle FROM signal_configs WHERE id = ?", (config["id"],))
            row = await cursor.fetchone()
        assert row[0] == 0  # must NOT be updated when data wasn't ready

    @pytest.mark.asyncio
    async def test_skip_entry_when_active_trade_exists(self):
        """If a sim_trade is already open for this config, no new signal is created."""
        await _setup_db()
        config = await _insert_config()
        df, breakout_time, _ = _make_breakout_df()
        fake_now = breakout_time + STEP_MS + 1

        # Insert an existing open trade
        now = _now_iso()
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price,
                     status, created_at)
                   VALUES (?, 'BTCUSDT', '1h', 'breakout', 'long', ?, 96.04, 'active', ?)""",
                (config["id"], BASE_TIME, now),
            )
            sig_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO sim_trades
                    (signal_id, config_id, symbol, interval, side,
                     stop_base, status, portfolio, invested_amount,
                     leverage, fees, entry_price, entry_time, quantity,
                     created_at, updated_at)
                   VALUES (?, ?, 'BTCUSDT', '1h', 'long', 96.04, 'open',
                           10000.0, 10000.0, 1.0, 0.0, 100.0, ?, 100.0, ?, ?)""",
                (sig_id, config["id"], BASE_TIME + STEP_MS, now, now),
            )
            await db.commit()

        with (
            patch("backend.signal_engine._now_ms", return_value=fake_now),
            patch("backend.signal_engine.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.signal_engine.load_candles_df", new=AsyncMock(return_value=df)),
        ):
            await scan_config(config)

        # Only the pre-existing signal should exist
        signals = await _get_signals()
        assert len(signals) == 1
        assert signals[0]["status"] == "active"  # unchanged


# ---------------------------------------------------------------------------
# Tests: entry fill (_fill_pending_entries)
# ---------------------------------------------------------------------------


class TestEntryFill:
    @pytest.mark.asyncio
    async def test_fills_when_next_candle_in_db(self):
        """pending_entry trade is filled at next candle open price when kline exists."""
        await _setup_db()
        config = await _insert_config()
        trigger_time = BASE_TIME
        next_open = trigger_time + STEP_MS
        entry_price = 125.0

        now = _now_iso()
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price,
                     status, created_at)
                   VALUES (?, 'BTCUSDT', '1h', 'breakout', 'long', ?, 96.04, 'pending', ?)""",
                (config["id"], trigger_time, now),
            )
            sig_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO sim_trades
                    (signal_id, config_id, symbol, interval, side,
                     stop_base, status, portfolio, invested_amount,
                     leverage, fees, created_at, updated_at)
                   VALUES (?, ?, 'BTCUSDT', '1h', 'long', 96.04, 'pending_entry',
                           10000.0, 10000.0, 1.0, 0.0, ?, ?)""",
                (sig_id, config["id"], now, now),
            )
            await db.commit()

        await _insert_kline(
            {"open_time": next_open, "open": entry_price, "high": 128.0, "low": 123.0, "close": 126.0, "volume": 1000.0}
        )

        with patch("backend.live_tracker.ensure_candles", new=AsyncMock(return_value=True)):
            await _fill_pending_entries()

        trades = await _get_sim_trades(status="open")
        assert len(trades) == 1
        assert trades[0]["entry_price"] == pytest.approx(entry_price)
        assert trades[0]["entry_time"] == next_open
        assert trades[0]["quantity"] is not None and trades[0]["quantity"] > 0

    @pytest.mark.asyncio
    async def test_does_not_fill_when_candle_missing_and_not_past_grace(self):
        """Trade stays pending_entry when next candle is missing and grace period hasn't passed."""
        await _setup_db()
        config = await _insert_config()
        trigger_time = BASE_TIME

        now = _now_iso()
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price,
                     status, created_at)
                   VALUES (?, 'BTCUSDT', '1h', 'breakout', 'long', ?, 96.04, 'pending', ?)""",
                (config["id"], trigger_time, now),
            )
            sig_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO sim_trades
                    (signal_id, config_id, symbol, interval, side,
                     stop_base, status, portfolio, invested_amount,
                     leverage, fees, created_at, updated_at)
                   VALUES (?, ?, 'BTCUSDT', '1h', 'long', 96.04, 'pending_entry',
                           10000.0, 10000.0, 1.0, 0.0, ?, ?)""",
                (sig_id, config["id"], now, now),
            )
            await db.commit()

        # No kline inserted; fake clock is before grace period (5s after next_open)
        next_open = trigger_time + STEP_MS
        fake_now = next_open + 1000  # 1s after next_open, before 5s grace

        with (
            patch("backend.live_tracker.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.live_tracker._now_ms", return_value=fake_now),
        ):
            await _fill_pending_entries()

        trades = await _get_sim_trades()
        assert trades[0]["status"] == "pending_entry"
        assert trades[0]["entry_price"] is None

    @pytest.mark.asyncio
    async def test_close_current_fills_at_trigger_candle_close(self):
        """modo_ejecucion=close_current fills at the Close of the trigger candle itself."""
        trigger_time = BASE_TIME
        trigger_close = 123.5
        next_open = 200.0  # much higher — asserts we did NOT use it

        await _setup_db()
        config = await _insert_config(
            params=json.dumps(
                {
                    "N_entrada": 5,
                    "M_salida": 3,
                    "stop_pct": 0.02,
                    "salida_por_ruptura": True,
                    "modo_ejecucion": "close_current",
                },
                sort_keys=True,
            )
        )

        now = _now_iso()
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price,
                     status, created_at)
                   VALUES (?, 'BTCUSDT', '1h', 'breakout', 'long', ?, 96.04, 'pending', ?)""",
                (config["id"], trigger_time, now),
            )
            sig_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO sim_trades
                    (signal_id, config_id, symbol, interval, side,
                     stop_base, status, portfolio, invested_amount,
                     leverage, fees, created_at, updated_at)
                   VALUES (?, ?, 'BTCUSDT', '1h', 'long', 96.04, 'pending_entry',
                           10000.0, 10000.0, 1.0, 0.0, ?, ?)""",
                (sig_id, config["id"], now, now),
            )
            await db.commit()

        # Insert the trigger candle (with a distinct close) AND the next candle
        # (with a wildly different open) — the fill must pick the trigger close.
        await _insert_kline(
            {
                "open_time": trigger_time,
                "open": 100.0,
                "high": 130.0,
                "low": 99.0,
                "close": trigger_close,
                "volume": 1000.0,
            }
        )
        await _insert_kline(
            {
                "open_time": trigger_time + STEP_MS,
                "open": next_open,
                "high": 205.0,
                "low": 199.0,
                "close": 203.0,
                "volume": 1000.0,
            }
        )

        with patch("backend.live_tracker.ensure_candles", new=AsyncMock(return_value=True)):
            await _fill_pending_entries()

        trades = await _get_sim_trades(status="open")
        assert len(trades) == 1
        assert trades[0]["entry_price"] == pytest.approx(trigger_close)
        assert trades[0]["entry_time"] == trigger_time  # NOT next_candle_open
        assert trades[0]["quantity"] == pytest.approx(10000.0 / trigger_close)

    @pytest.mark.asyncio
    async def test_explicit_open_next_fills_at_next_candle_open(self):
        """modo_ejecucion=open_next (explicit) keeps the historical fill-at-next-open behaviour."""
        trigger_time = BASE_TIME
        trigger_close = 123.5
        next_open = 127.0

        await _setup_db()
        config = await _insert_config(
            params=json.dumps(
                {
                    "N_entrada": 5,
                    "M_salida": 3,
                    "stop_pct": 0.02,
                    "salida_por_ruptura": True,
                    "modo_ejecucion": "open_next",
                },
                sort_keys=True,
            )
        )

        now = _now_iso()
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price,
                     status, created_at)
                   VALUES (?, 'BTCUSDT', '1h', 'breakout', 'long', ?, 96.04, 'pending', ?)""",
                (config["id"], trigger_time, now),
            )
            sig_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO sim_trades
                    (signal_id, config_id, symbol, interval, side,
                     stop_base, status, portfolio, invested_amount,
                     leverage, fees, created_at, updated_at)
                   VALUES (?, ?, 'BTCUSDT', '1h', 'long', 96.04, 'pending_entry',
                           10000.0, 10000.0, 1.0, 0.0, ?, ?)""",
                (sig_id, config["id"], now, now),
            )
            await db.commit()

        await _insert_kline(
            {
                "open_time": trigger_time,
                "open": 100.0,
                "high": 130.0,
                "low": 99.0,
                "close": trigger_close,
                "volume": 1000.0,
            }
        )
        await _insert_kline(
            {
                "open_time": trigger_time + STEP_MS,
                "open": next_open,
                "high": 130.0,
                "low": 125.0,
                "close": 128.0,
                "volume": 1000.0,
            }
        )

        with patch("backend.live_tracker.ensure_candles", new=AsyncMock(return_value=True)):
            await _fill_pending_entries()

        trades = await _get_sim_trades(status="open")
        assert len(trades) == 1
        assert trades[0]["entry_price"] == pytest.approx(next_open)
        assert trades[0]["entry_time"] == trigger_time + STEP_MS

    @pytest.mark.asyncio
    async def test_missing_modo_ejecucion_defaults_to_open_next(self):
        """Legacy configs without modo_ejecucion in params continue to fill at next open."""
        trigger_time = BASE_TIME
        next_open = 127.0

        await _setup_db()
        # Default _insert_config omits modo_ejecucion — mirrors old configs.
        config = await _insert_config()

        now = _now_iso()
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price,
                     status, created_at)
                   VALUES (?, 'BTCUSDT', '1h', 'breakout', 'long', ?, 96.04, 'pending', ?)""",
                (config["id"], trigger_time, now),
            )
            sig_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO sim_trades
                    (signal_id, config_id, symbol, interval, side,
                     stop_base, status, portfolio, invested_amount,
                     leverage, fees, created_at, updated_at)
                   VALUES (?, ?, 'BTCUSDT', '1h', 'long', 96.04, 'pending_entry',
                           10000.0, 10000.0, 1.0, 0.0, ?, ?)""",
                (sig_id, config["id"], now, now),
            )
            await db.commit()

        await _insert_kline(
            {
                "open_time": trigger_time + STEP_MS,
                "open": next_open,
                "high": 130.0,
                "low": 125.0,
                "close": 128.0,
                "volume": 1000.0,
            }
        )

        with patch("backend.live_tracker.ensure_candles", new=AsyncMock(return_value=True)):
            await _fill_pending_entries()

        trades = await _get_sim_trades(status="open")
        assert len(trades) == 1
        assert trades[0]["entry_price"] == pytest.approx(next_open)
        assert trades[0]["entry_time"] == trigger_time + STEP_MS


# ---------------------------------------------------------------------------
# Tests: candle-close exit (_check_candle_close_exits)
# ---------------------------------------------------------------------------


class TestCandleCloseExit:
    @pytest.mark.asyncio
    async def test_exit_signal_closes_trade_at_close_price(self):
        """Strategy exit_long signal closes the trade at candle close price."""
        await _setup_db()
        config = await _insert_config()

        stop_base = 96.04
        entry_price = 125.0
        entry_time = BASE_TIME + STEP_MS

        now = _now_iso()
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price,
                     status, created_at)
                   VALUES (?, 'BTCUSDT', '1h', 'breakout', 'long', ?, ?, 'active', ?)""",
                (config["id"], BASE_TIME, stop_base, now),
            )
            sig_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO sim_trades
                    (signal_id, config_id, symbol, interval, side,
                     entry_price, entry_time, stop_base, status,
                     portfolio, invested_amount, leverage, quantity, fees,
                     created_at, updated_at)
                   VALUES (?, ?, 'BTCUSDT', '1h', 'long', ?, ?, ?, 'open',
                           10000.0, 10000.0, 1.0, 80.0, 0.0, ?, ?)""",
                (sig_id, config["id"], entry_price, entry_time, stop_base, now, now),
            )
            await db.commit()

        # Build DataFrame ending with an exit candle
        # Post-entry candles with descending lows: 120, 115, 110 (indices 32-34)
        # min_exit at index 35 = min(120, 115, 110) = 110
        # Exit candle close=108 < 110 → exit_long; low=105 > stop_base=96.04 → no stop
        flat = _make_flat_candles(30)
        breakout_t = flat[-1]["open_time"] + STEP_MS
        candles = flat + [
            {"open_time": breakout_t, "open": 100.0, "high": 130.0, "low": 99.0, "close": 125.0, "volume": 1000.0},
            {
                "open_time": breakout_t + STEP_MS,
                "open": 125.0,
                "high": 128.0,
                "low": 122.0,
                "close": 126.0,
                "volume": 1000.0,
            },
            {
                "open_time": breakout_t + 2 * STEP_MS,
                "open": 126.0,
                "high": 127.0,
                "low": 120.0,
                "close": 122.0,
                "volume": 1000.0,
            },
            {
                "open_time": breakout_t + 3 * STEP_MS,
                "open": 122.0,
                "high": 123.0,
                "low": 115.0,
                "close": 116.0,
                "volume": 1000.0,
            },
            {
                "open_time": breakout_t + 4 * STEP_MS,
                "open": 116.0,
                "high": 117.0,
                "low": 110.0,
                "close": 112.0,
                "volume": 1000.0,
            },
        ]
        exit_t = breakout_t + 5 * STEP_MS
        exit_candle = {
            "open_time": exit_t,
            "open": 112.0,
            "high": 114.0,
            "low": 105.0,
            "close": 108.0,
            "volume": 1000.0,
        }
        df = _candles_to_df(candles + [exit_candle])

        # current_open = exit_t + STEP_MS → last_closed = exit_t ✓
        fake_now = exit_t + STEP_MS + 1

        with (
            patch("backend.live_tracker._now_ms", return_value=fake_now),
            patch("backend.live_tracker.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.live_tracker.load_candles_df", new=AsyncMock(return_value=df)),
        ):
            await _check_candle_close_exits()

        trades = await _get_sim_trades(status="closed")
        assert len(trades) == 1
        assert trades[0]["exit_reason"] == "exit_signal"
        assert trades[0]["exit_price"] == pytest.approx(108.0)
        assert trades[0]["pnl"] < 0  # exited below entry

    @pytest.mark.asyncio
    async def test_stop_on_candle_close_fallback(self):
        """Stop detected via candle Low on close is labeled 'stop_candle'."""
        await _setup_db()
        config = await _insert_config()

        stop_base = 96.04
        entry_price = 125.0
        entry_time = BASE_TIME + STEP_MS

        now = _now_iso()
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price,
                     status, created_at)
                   VALUES (?, 'BTCUSDT', '1h', 'breakout', 'long', ?, ?, 'active', ?)""",
                (config["id"], BASE_TIME, stop_base, now),
            )
            sig_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO sim_trades
                    (signal_id, config_id, symbol, interval, side,
                     entry_price, entry_time, stop_base, status,
                     portfolio, invested_amount, leverage, quantity, fees,
                     created_at, updated_at)
                   VALUES (?, ?, 'BTCUSDT', '1h', 'long', ?, ?, ?, 'open',
                           10000.0, 10000.0, 1.0, 80.0, 0.0, ?, ?)""",
                (sig_id, config["id"], entry_price, entry_time, stop_base, now, now),
            )
            await db.commit()

        # Stop candle: low=90 ≤ stop_base=96.04 → stop_long fires
        flat = _make_flat_candles(30)
        breakout_t = flat[-1]["open_time"] + STEP_MS
        stop_t = breakout_t + STEP_MS
        stop_candle = {
            "open_time": stop_t,
            "open": 125.0,
            "high": 126.0,
            "low": 90.0,  # low <= stop_base (96.04) → stop_long
            "close": 97.0,
            "volume": 1000.0,
        }
        df = _candles_to_df(
            flat
            + [
                {"open_time": breakout_t, "open": 100.0, "high": 130.0, "low": 99.0, "close": 125.0, "volume": 1000.0},
                stop_candle,
            ]
        )

        fake_now = stop_t + STEP_MS + 1

        with (
            patch("backend.live_tracker._now_ms", return_value=fake_now),
            patch("backend.live_tracker.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.live_tracker.load_candles_df", new=AsyncMock(return_value=df)),
        ):
            await _check_candle_close_exits()

        trades = await _get_sim_trades(status="closed")
        assert len(trades) == 1
        assert trades[0]["exit_reason"] == "stop_candle"
        # Gap fill: open=125 > stop_base=96.04 → exec at stop_base
        assert trades[0]["exit_price"] == pytest.approx(stop_base)
        assert trades[0]["pnl"] < 0

    @pytest.mark.asyncio
    async def test_no_exit_when_candle_does_not_trigger(self):
        """If neither stop nor exit condition is met, trade stays open."""
        await _setup_db()
        config = await _insert_config()

        stop_base = 96.04
        entry_price = 125.0
        entry_time = BASE_TIME + STEP_MS

        now = _now_iso()
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO signals
                    (config_id, symbol, interval, strategy, side,
                     trigger_candle_time, stop_price,
                     status, created_at)
                   VALUES (?, 'BTCUSDT', '1h', 'breakout', 'long', ?, ?, 'active', ?)""",
                (config["id"], BASE_TIME, stop_base, now),
            )
            sig_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO sim_trades
                    (signal_id, config_id, symbol, interval, side,
                     entry_price, entry_time, stop_base, status,
                     portfolio, invested_amount, leverage, quantity, fees,
                     created_at, updated_at)
                   VALUES (?, ?, 'BTCUSDT', '1h', 'long', ?, ?, ?, 'open',
                           10000.0, 10000.0, 1.0, 80.0, 0.0, ?, ?)""",
                (sig_id, config["id"], entry_price, entry_time, stop_base, now, now),
            )
            await db.commit()

        # Neutral candle: close=126, low=122 — neither stop nor exit triggers
        flat = _make_flat_candles(30)
        breakout_t = flat[-1]["open_time"] + STEP_MS
        neutral_t = breakout_t + STEP_MS
        neutral_candle = {
            "open_time": neutral_t,
            "open": 125.0,
            "high": 128.0,
            "low": 122.0,
            "close": 126.0,
            "volume": 1000.0,
        }
        df = _candles_to_df(
            flat
            + [
                {"open_time": breakout_t, "open": 100.0, "high": 130.0, "low": 99.0, "close": 125.0, "volume": 1000.0},
                neutral_candle,
            ]
        )

        fake_now = neutral_t + STEP_MS + 1

        with (
            patch("backend.live_tracker._now_ms", return_value=fake_now),
            patch("backend.live_tracker.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.live_tracker.load_candles_df", new=AsyncMock(return_value=df)),
        ):
            await _check_candle_close_exits()

        trades = await _get_sim_trades(status="open")
        assert len(trades) == 1  # still open


# ---------------------------------------------------------------------------
# Tests: full end-to-end trade cycles
# ---------------------------------------------------------------------------


class TestFullTradeCycle:
    @pytest.mark.asyncio
    async def test_full_cycle_breakout_to_intrabar_stop(self):
        """Complete flow: scan detects breakout → entry filled → intrabar stop fires."""
        await _setup_db()
        config = await _insert_config()
        df_scan, breakout_time, stop_base = _make_breakout_df()

        # --- Step 1: Scan detects breakout ---
        fake_now_scan = breakout_time + STEP_MS + 1

        with (
            patch("backend.signal_engine._now_ms", return_value=fake_now_scan),
            patch("backend.signal_engine.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.signal_engine.load_candles_df", new=AsyncMock(return_value=df_scan)),
        ):
            await scan_config(config)

        signals = await _get_signals()
        assert len(signals) == 1 and signals[0]["side"] == "long"
        trades = await _get_sim_trades()
        assert len(trades) == 1 and trades[0]["status"] == "pending_entry"

        # --- Step 2: Next candle opens → fill entry ---
        next_open = breakout_time + STEP_MS
        entry_price = 125.0
        await _insert_kline(
            {"open_time": next_open, "open": entry_price, "high": 128.0, "low": 122.0, "close": 126.0, "volume": 1000.0}
        )

        with patch("backend.live_tracker.ensure_candles", new=AsyncMock(return_value=True)):
            await _fill_pending_entries()

        open_trades = await _get_sim_trades(status="open")
        assert len(open_trades) == 1
        assert open_trades[0]["entry_price"] == pytest.approx(entry_price)
        assert open_trades[0]["quantity"] > 0

        # --- Step 3: Price crosses stop_base → stop fired (gap fill at price) ---
        below_stop = open_trades[0]["stop_base"] - 1.0

        with patch("backend.live_tracker.binance_client", _mock_binance(ticker_price=below_stop)):
            await _check_intrabar_stops()

        closed = await _get_sim_trades(status="closed")
        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "stop_intrabar"
        assert closed[0]["exit_price"] == pytest.approx(below_stop)
        assert closed[0]["pnl"] < 0

    @pytest.mark.asyncio
    async def test_full_cycle_breakout_to_exit_signal(self):
        """Complete flow: scan detects breakout → entry filled → candle-close exit signal."""
        await _setup_db()
        config = await _insert_config()
        df_scan, breakout_time, _ = _make_breakout_df()

        # --- Step 1: Scan ---
        fake_now_scan = breakout_time + STEP_MS + 1

        with (
            patch("backend.signal_engine._now_ms", return_value=fake_now_scan),
            patch("backend.signal_engine.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.signal_engine.load_candles_df", new=AsyncMock(return_value=df_scan)),
        ):
            await scan_config(config)

        # --- Step 2: Fill entry ---
        next_open = breakout_time + STEP_MS
        entry_price = 125.0
        await _insert_kline(
            {"open_time": next_open, "open": entry_price, "high": 128.0, "low": 122.0, "close": 126.0, "volume": 1000.0}
        )

        with patch("backend.live_tracker.ensure_candles", new=AsyncMock(return_value=True)):
            await _fill_pending_entries()

        assert len(await _get_sim_trades(status="open")) == 1

        # --- Step 3: Intrabar check does NOT fire (price above stop) ---
        with patch("backend.live_tracker.binance_client", _mock_binance(ticker_price=120.0)):
            await _check_intrabar_stops()

        assert len(await _get_sim_trades(status="open")) == 1  # still open

        # --- Step 4: Advance to exit candle ---
        # Post-entry candles with descending lows to build up the exit trigger
        flat = _make_flat_candles(30)
        breakout_candle = {
            "open_time": breakout_time,
            "open": 100.0,
            "high": 130.0,
            "low": 99.0,
            "close": 125.0,
            "volume": 1000.0,
        }
        post_candles = [
            {"open_time": next_open, "open": 125.0, "high": 128.0, "low": 122.0, "close": 126.0, "volume": 1000.0},
            {
                "open_time": next_open + STEP_MS,
                "open": 126.0,
                "high": 127.0,
                "low": 120.0,
                "close": 122.0,
                "volume": 1000.0,
            },
            {
                "open_time": next_open + 2 * STEP_MS,
                "open": 122.0,
                "high": 123.0,
                "low": 115.0,
                "close": 116.0,
                "volume": 1000.0,
            },
            {
                "open_time": next_open + 3 * STEP_MS,
                "open": 116.0,
                "high": 117.0,
                "low": 110.0,
                "close": 112.0,
                "volume": 1000.0,
            },
        ]
        # exit_candle: close=108 < min_exit=110, low=105 > stop_base=96.04
        exit_t = next_open + 4 * STEP_MS
        exit_candle = {
            "open_time": exit_t,
            "open": 112.0,
            "high": 114.0,
            "low": 105.0,
            "close": 108.0,
            "volume": 1000.0,
        }
        df_exit = _candles_to_df(flat + [breakout_candle] + post_candles + [exit_candle])

        fake_now_exit = exit_t + STEP_MS + 1

        with (
            patch("backend.live_tracker._now_ms", return_value=fake_now_exit),
            patch("backend.live_tracker.ensure_candles", new=AsyncMock(return_value=True)),
            patch("backend.live_tracker.load_candles_df", new=AsyncMock(return_value=df_exit)),
        ):
            await _check_candle_close_exits()

        closed = await _get_sim_trades(status="closed")
        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "exit_signal"
        assert closed[0]["exit_price"] == pytest.approx(108.0)
        assert closed[0]["pnl"] < 0
