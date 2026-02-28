"""Database abstraction layer: supports SQLite (local dev) and PostgreSQL (k3s dev).

Backend selection is driven by the DATABASE_URL environment variable:
  - Not set / sqlite:///...  → aiosqlite (default, local development)
  - postgresql://...         → asyncpg (k3s dev cluster, schema managed by Alembic)

Both backends expose the same get_db() async context manager.
SQLite init_db() creates the schema on first run; PostgreSQL relies on Alembic migrations.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from backend.config import DATABASE_URL, DB_PATH, IS_POSTGRES

# ---------------------------------------------------------------------------
# Placeholder translation: SQLite uses ?, PostgreSQL uses $1 $2 ...
# ---------------------------------------------------------------------------


def _to_pg_placeholders(query: str) -> str:
    """Replace all ? placeholders with $1, $2, ... for asyncpg."""
    counter = 0

    def replacer(_match: re.Match) -> str:
        nonlocal counter
        counter += 1
        return f"${counter}"

    return re.sub(r"\?", replacer, query)


# ---------------------------------------------------------------------------
# PostgreSQL connection wrapper (asyncpg)
# ---------------------------------------------------------------------------


class _PgConnection:
    """Thin wrapper around asyncpg Connection that mimics the aiosqlite API."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._rows: list[dict] | None = None
        self._last_id: int | None = None

    async def execute(self, query: str, params: tuple | list = ()) -> _PgCursor:
        pg_query = _to_pg_placeholders(query)
        q_upper = query.strip().upper()
        if q_upper.startswith("INSERT") and "RETURNING" not in q_upper:
            pg_query = pg_query.rstrip().rstrip(";") + " RETURNING id"
            row = await self._conn.fetchrow(pg_query, *params)
            self._last_id = row["id"] if row else None
            return _PgCursor([], self._last_id)
        rows = await self._conn.fetch(pg_query, *params)
        records = [dict(r) for r in rows]
        return _PgCursor(records, None)

    async def executemany(self, query: str, params_seq: list) -> None:
        pg_query = _to_pg_placeholders(query)
        async with self._conn.transaction():
            for params in params_seq:
                await self._conn.execute(pg_query, *params)

    async def commit(self) -> None:
        pass

    @property
    def lastrowid(self) -> int | None:
        return self._last_id


class _PgCursor:
    def __init__(self, rows: list[dict], lastrowid: int | None) -> None:
        self._rows = rows
        self.lastrowid = lastrowid
        self.description = [(k,) for k in (rows[0].keys() if rows else [])]

    async def fetchall(self) -> list[dict]:
        return self._rows

    async def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


@asynccontextmanager
async def _get_pg_db() -> AsyncIterator[_PgConnection]:
    import asyncpg  # noqa: PLC0415

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        yield _PgConnection(conn)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# SQLite connection wrapper
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _get_sqlite_db() -> AsyncIterator[aiosqlite.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@asynccontextmanager
async def get_db():
    """Async context manager returning a database connection.

    Yields an aiosqlite.Connection for SQLite or a _PgConnection for PostgreSQL.
    Both expose: execute(), executemany(), commit(), and cursor.fetchall/fetchone.
    """
    if IS_POSTGRES:
        async with _get_pg_db() as db:
            yield db
    else:
        async with _get_sqlite_db() as db:
            yield db


async def init_db() -> None:
    """Create all tables for SQLite local development.

    For PostgreSQL the schema is managed by Alembic migrations — this function
    is a no-op when DATABASE_URL points to PostgreSQL.
    """
    if IS_POSTGRES:
        return

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

            CREATE TABLE IF NOT EXISTS signal_configs (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol                  TEXT    NOT NULL,
                interval                TEXT    NOT NULL,
                strategy                TEXT    NOT NULL,
                params                  TEXT    NOT NULL DEFAULT '{}',
                stop_cross_pct          REAL    NOT NULL DEFAULT 0.02,
                portfolio               REAL    NOT NULL DEFAULT 10000.0,
                invested_amount         REAL,
                leverage                REAL,
                cost_bps                REAL    NOT NULL DEFAULT 10.0,
                polling_interval_s      INTEGER,
                active                  INTEGER NOT NULL DEFAULT 1,
                last_processed_candle   INTEGER DEFAULT 0,
                created_at              TEXT    NOT NULL,
                updated_at              TEXT    NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_configs_unique
                ON signal_configs (symbol, interval, strategy, params);

            CREATE TABLE IF NOT EXISTS signals (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                config_id               INTEGER NOT NULL REFERENCES signal_configs(id),
                symbol                  TEXT    NOT NULL,
                interval                TEXT    NOT NULL,
                strategy                TEXT    NOT NULL,
                side                    TEXT    NOT NULL,
                trigger_candle_time     INTEGER NOT NULL,
                stop_price              REAL    NOT NULL,
                stop_trigger_price      REAL    NOT NULL,
                status                  TEXT    NOT NULL DEFAULT 'pending',
                created_at              TEXT    NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_dedup
                ON signals (config_id, trigger_candle_time);
            CREATE INDEX IF NOT EXISTS idx_signals_config
                ON signals (config_id);

            CREATE TABLE IF NOT EXISTS sim_trades (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id               INTEGER NOT NULL REFERENCES signals(id),
                config_id               INTEGER NOT NULL REFERENCES signal_configs(id),
                symbol                  TEXT    NOT NULL,
                interval                TEXT    NOT NULL,
                side                    TEXT    NOT NULL,
                entry_price             REAL,
                entry_time              INTEGER,
                stop_base               REAL    NOT NULL,
                stop_trigger            REAL    NOT NULL,
                exit_price              REAL,
                exit_time               INTEGER,
                exit_reason             TEXT,
                status                  TEXT    NOT NULL DEFAULT 'pending_entry',
                portfolio               REAL    NOT NULL,
                invested_amount         REAL    NOT NULL,
                leverage                REAL    NOT NULL,
                quantity                REAL,
                pnl                     REAL,
                pnl_pct                 REAL,
                fees                    REAL,
                equity_peak             REAL,
                created_at              TEXT    NOT NULL,
                updated_at              TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sim_trades_status
                ON sim_trades (status);
            CREATE INDEX IF NOT EXISTS idx_sim_trades_config
                ON sim_trades (config_id);

            CREATE TABLE IF NOT EXISTS real_trades (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                sim_trade_id            INTEGER REFERENCES sim_trades(id),
                signal_id               INTEGER REFERENCES signals(id),
                symbol                  TEXT    NOT NULL,
                side                    TEXT    NOT NULL,
                entry_price             REAL    NOT NULL,
                entry_time              TEXT    NOT NULL,
                exit_price              REAL,
                exit_time               TEXT,
                quantity                REAL    NOT NULL,
                fees                    REAL    DEFAULT 0.0,
                pnl                     REAL,
                pnl_pct                 REAL,
                notes                   TEXT,
                status                  TEXT    NOT NULL DEFAULT 'open',
                created_at              TEXT    NOT NULL,
                updated_at              TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_real_trades_sim
                ON real_trades (sim_trade_id);

            CREATE TABLE IF NOT EXISTS notification_log (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type              TEXT    NOT NULL,
                reference_type          TEXT    NOT NULL,
                reference_id            INTEGER NOT NULL,
                message                 TEXT,
                sent_at                 TEXT    NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_dedup
                ON notification_log (event_type, reference_type, reference_id);
        """)
        await db.commit()
