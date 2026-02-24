"""SQLite database setup with aiosqlite. Schema creation on startup."""
from __future__ import annotations

import aiosqlite
import os
from contextlib import asynccontextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", "data/trading_tools.db"))


@asynccontextmanager
async def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


async def init_db() -> None:
    """Create all tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript("""
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

            CREATE TABLE IF NOT EXISTS download_jobs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol              TEXT    NOT NULL,
                interval            TEXT    NOT NULL,
                start_time          INTEGER NOT NULL,
                end_time            INTEGER NOT NULL,
                status              TEXT    NOT NULL DEFAULT 'pending',
                progress_pct        REAL    DEFAULT 0.0,
                candles_downloaded  INTEGER DEFAULT 0,
                candles_expected    INTEGER DEFAULT 0,
                gaps_found          INTEGER DEFAULT 0,
                created_at          TEXT    NOT NULL,
                updated_at          TEXT    NOT NULL,
                log                 TEXT    DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS derived_metrics (
                symbol              TEXT    NOT NULL,
                interval            TEXT    NOT NULL,
                open_time           INTEGER NOT NULL,
                metric_name         TEXT    NOT NULL,
                value               REAL,
                PRIMARY KEY (symbol, interval, open_time, metric_name)
            );

            CREATE INDEX IF NOT EXISTS idx_klines_symbol_interval
                ON klines (symbol, interval);
            CREATE INDEX IF NOT EXISTS idx_klines_open_time
                ON klines (open_time);
            CREATE INDEX IF NOT EXISTS idx_derived_symbol_interval
                ON derived_metrics (symbol, interval);
        """)
        await db.commit()
