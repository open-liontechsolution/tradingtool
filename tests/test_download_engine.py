"""Tests for download_engine: gap detection, dedup, upsert logic."""

from __future__ import annotations

import os

import aiosqlite
import pytest
import pytest_asyncio

os.environ["DB_PATH"] = ":memory:"

from backend.download_engine import (
    INTERVAL_MS,
    _expected_open_times,
    _get_existing_open_times,
    _upsert_candles,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
    symbol              TEXT    NOT NULL,
    interval            TEXT    NOT NULL,
    open_time           INTEGER NOT NULL,
    open                TEXT    NOT NULL,
    high                TEXT    NOT NULL,
    low                 TEXT    NOT NULL,
    close               TEXT    NOT NULL,
    volume              TEXT    NOT NULL,
    close_time          INTEGER NOT NULL,
    quote_asset_volume  TEXT    NOT NULL,
    number_of_trades    INTEGER NOT NULL,
    taker_buy_base_vol  TEXT    NOT NULL,
    taker_buy_quote_vol TEXT    NOT NULL,
    ignore_field        TEXT,
    source              TEXT    DEFAULT 'binance_spot',
    downloaded_at       TEXT    NOT NULL,
    PRIMARY KEY (symbol, interval, open_time)
);
"""


def _make_candle(symbol: str, interval: str, open_time: int) -> dict:
    return {
        "symbol": symbol,
        "interval": interval,
        "open_time": open_time,
        "open": "100.0",
        "high": "110.0",
        "low": "90.0",
        "close": "105.0",
        "volume": "1000.0",
        "close_time": open_time + INTERVAL_MS[interval] - 1,
        "quote_asset_volume": "100000.0",
        "number_of_trades": 500,
        "taker_buy_base_vol": "500.0",
        "taker_buy_quote_vol": "50000.0",
        "ignore_field": "0",
        "source": "binance_spot",
        "downloaded_at": "2024-01-01T00:00:00+00:00",
    }


@pytest_asyncio.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(SCHEMA)
    await conn.commit()
    yield conn
    await conn.close()


# ---------------------------------------------------------------------------
# _expected_open_times
# ---------------------------------------------------------------------------


class TestExpectedOpenTimes:
    def test_1h_simple_range(self):
        step = INTERVAL_MS["1h"]
        start = 0
        end = step * 5
        times = _expected_open_times(start, end, "1h")
        assert times == [0, step, step * 2, step * 3, step * 4]

    def test_aligned_start_already_on_boundary(self):
        step = INTERVAL_MS["1d"]
        start = step * 10
        end = step * 13
        times = _expected_open_times(start, end, "1d")
        assert times == [step * 10, step * 11, step * 12]

    def test_start_between_boundaries_aligns_up(self):
        step = INTERVAL_MS["1h"]
        start = step // 2  # half-hour in — should align up to step
        end = step * 3
        times = _expected_open_times(start, end, "1h")
        assert times[0] == step
        assert len(times) == 2  # step and step*2

    def test_empty_range(self):
        step = INTERVAL_MS["1h"]
        times = _expected_open_times(step * 5, step * 5, "1h")
        assert times == []

    def test_unknown_interval_raises(self):
        with pytest.raises(ValueError, match="Unknown interval"):
            _expected_open_times(0, 1_000_000, "99x")


# ---------------------------------------------------------------------------
# _get_existing_open_times
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_existing_empty(db):
    result = await _get_existing_open_times(db, "BTCUSDT", "1h", 0, 10**13)
    assert result == set()


@pytest.mark.asyncio
async def test_get_existing_returns_correct_times(db):
    step = INTERVAL_MS["1h"]
    candles = [_make_candle("BTCUSDT", "1h", step * i) for i in range(5)]
    await _upsert_candles(db, candles)

    result = await _get_existing_open_times(db, "BTCUSDT", "1h", 0, step * 5)
    assert result == {step * i for i in range(5)}


@pytest.mark.asyncio
async def test_get_existing_filters_by_range(db):
    step = INTERVAL_MS["1h"]
    candles = [_make_candle("BTCUSDT", "1h", step * i) for i in range(10)]
    await _upsert_candles(db, candles)

    result = await _get_existing_open_times(db, "BTCUSDT", "1h", step * 3, step * 7)
    assert result == {step * 3, step * 4, step * 5, step * 6}


# ---------------------------------------------------------------------------
# _upsert_candles — deduplication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_no_duplicates(db):
    step = INTERVAL_MS["1h"]
    candle = _make_candle("BTCUSDT", "1h", step)

    count1 = await _upsert_candles(db, [candle])
    count2 = await _upsert_candles(db, [candle])  # same candle again

    assert count1 == 1
    assert count2 == 1

    cursor = await db.execute("SELECT COUNT(*) FROM klines")
    row = await cursor.fetchone()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_upsert_replaces_on_conflict(db):
    step = INTERVAL_MS["1h"]
    candle = _make_candle("BTCUSDT", "1h", step)
    await _upsert_candles(db, [candle])

    updated = dict(candle)
    updated["close"] = "999.0"
    await _upsert_candles(db, [updated])

    cursor = await db.execute("SELECT close FROM klines WHERE open_time=?", (step,))
    row = await cursor.fetchone()
    assert row[0] == "999.0"


@pytest.mark.asyncio
async def test_upsert_multiple_symbols_independent(db):
    step = INTERVAL_MS["1h"]
    btc = _make_candle("BTCUSDT", "1h", step)
    eth = _make_candle("ETHUSDT", "1h", step)
    eth["open_time"] = step

    await _upsert_candles(db, [btc, eth])

    cursor = await db.execute("SELECT COUNT(*) FROM klines")
    row = await cursor.fetchone()
    assert row[0] == 2


@pytest.mark.asyncio
async def test_upsert_empty_list(db):
    count = await _upsert_candles(db, [])
    assert count == 0


# ---------------------------------------------------------------------------
# Gap detection logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gap_detection(db):
    step = INTERVAL_MS["1h"]
    # Insert candles at t=0,1,2,4,5 (missing t=3)
    present = [0, 1, 2, 4, 5]
    candles = [_make_candle("BTCUSDT", "1h", step * i) for i in present]
    await _upsert_candles(db, candles)

    expected = set(_expected_open_times(0, step * 6, "1h"))
    existing = await _get_existing_open_times(db, "BTCUSDT", "1h", 0, step * 6)
    gaps = expected - existing

    assert gaps == {step * 3}
