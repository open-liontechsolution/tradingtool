"""Database abstraction layer: supports SQLite (local dev) and PostgreSQL (k3s dev).

Backend selection is driven by the DATABASE_URL environment variable:
  - Not set / sqlite:///...  → aiosqlite (default, local development)
  - postgresql://...         → asyncpg (k3s dev cluster, schema managed by Alembic)

Both backends expose the same get_db() async context manager.
SQLite init_db() creates the schema on first run; PostgreSQL relies on Alembic migrations.
"""

from __future__ import annotations

import logging
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


class _Row:
    """Row that supports both integer-index and string-key access.

    This bridges the gap between aiosqlite (which returns sqlite3.Row objects
    supporting ``row[0]`` and ``row['col']``) and asyncpg (which returns
    Record objects that are converted to plain dicts losing index access).
    Iteration yields *values* so ``zip(cols, row)`` works as expected.
    """

    __slots__ = ("_keys", "_values", "_map")

    def __init__(self, mapping: dict) -> None:
        self._keys = list(mapping.keys())
        self._values = [mapping[k] for k in self._keys]
        self._map = mapping

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._map[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return self._keys

    def values(self):
        return self._values

    def items(self):
        return zip(self._keys, self._values, strict=False)

    def get(self, key: str, default: Any = None) -> Any:
        return self._map.get(key, default)


class _PgConnection:
    """Thin wrapper around asyncpg Connection that mimics the aiosqlite API."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._rows: list[_Row] | None = None
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
        records = [_Row(dict(r)) for r in rows]
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
    def __init__(self, rows: list[_Row], lastrowid: int | None) -> None:
        self._rows = rows
        self.lastrowid = lastrowid
        self.description = [(k,) for k in (rows[0].keys() if rows else [])]

    async def fetchall(self) -> list[_Row]:
        return self._rows

    async def fetchone(self) -> _Row | None:
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


def _run_alembic_upgrade_sync() -> None:
    """Run ``alembic upgrade head`` in a **fresh** event loop (must be called outside asyncio)."""
    from alembic.config import Config  # noqa: PLC0415

    from alembic import command  # noqa: PLC0415

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


async def init_db() -> None:
    """Initialise the database.

    - **PostgreSQL**: runs Alembic ``upgrade head`` so migrations are applied
      automatically on every deployment.
    - **SQLite**: creates all tables inline (no Alembic).
    """
    if IS_POSTGRES:
        import asyncio  # noqa: PLC0415

        log = logging.getLogger(__name__)
        log.info("Running Alembic migrations (upgrade head) ...")
        await asyncio.to_thread(_run_alembic_upgrade_sync)
        log.info("Alembic migrations complete.")
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

            CREATE TABLE IF NOT EXISTS users (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                keycloak_sub            TEXT    NOT NULL UNIQUE,
                email                   TEXT,
                username                TEXT,
                roles                   TEXT    NOT NULL DEFAULT '[]',
                created_at              TEXT    NOT NULL,
                last_login_at           TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS signal_configs (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                 INTEGER REFERENCES users(id),
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
                channel                 TEXT    NOT NULL DEFAULT 'internal',
                user_id                 INTEGER REFERENCES users(id),
                message                 TEXT,
                sent_at                 TEXT    NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_dedup
                ON notification_log (event_type, reference_type, reference_id, channel);

            CREATE TABLE IF NOT EXISTS telegram_link_tokens (
                token                   TEXT    PRIMARY KEY,
                user_id                 INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at              TEXT    NOT NULL,
                expires_at              TEXT    NOT NULL,
                used_at                 TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_telegram_link_tokens_user
                ON telegram_link_tokens (user_id);

            CREATE TABLE IF NOT EXISTS sim_trade_stop_moves (
                id                 INTEGER PRIMARY KEY,
                sim_trade_id       INTEGER NOT NULL REFERENCES sim_trades(id) ON DELETE CASCADE,
                prev_stop_base     REAL    NOT NULL,
                new_stop_base      REAL    NOT NULL,
                prev_stop_trigger  REAL    NOT NULL,
                new_stop_trigger   REAL    NOT NULL,
                candle_time        INTEGER NOT NULL,
                created_at         TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sim_trade_stop_moves_trade
                ON sim_trade_stop_moves (sim_trade_id);
        """)
        await db.commit()

        # ------------------------------------------------------------------
        # SQLite migrations for existing databases
        # ------------------------------------------------------------------
        # Add user_id to signal_configs if it doesn't exist yet (existing DBs).
        cursor = await db.execute("PRAGMA table_info(signal_configs)")
        sc_columns = {row[1] for row in await cursor.fetchall()}
        if "user_id" not in sc_columns:
            await db.execute("ALTER TABLE signal_configs ADD COLUMN user_id INTEGER REFERENCES users(id)")
            await db.commit()
        if "telegram_enabled" not in sc_columns:
            await db.execute("ALTER TABLE signal_configs ADD COLUMN telegram_enabled INTEGER NOT NULL DEFAULT 0")
            await db.commit()

        # Ensure index exists (safe even if column was just added)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_signal_configs_user ON signal_configs (user_id)")
        await db.commit()

        # --- users: Telegram link columns --------------------------------
        cursor = await db.execute("PRAGMA table_info(users)")
        u_columns = {row[1] for row in await cursor.fetchall()}
        if "telegram_chat_id" not in u_columns:
            await db.execute("ALTER TABLE users ADD COLUMN telegram_chat_id INTEGER")
            await db.execute("ALTER TABLE users ADD COLUMN telegram_username TEXT")
            await db.execute("ALTER TABLE users ADD COLUMN telegram_linked_at TEXT")
            await db.commit()
        # Unique on telegram_chat_id (SQLite allows multiple NULLs by default).
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_chat_id ON users (telegram_chat_id)")
        await db.commit()

        # --- notification_log: channel + user_id + swap unique index -----
        cursor = await db.execute("PRAGMA table_info(notification_log)")
        nl_columns = {row[1] for row in await cursor.fetchall()}
        if "channel" not in nl_columns:
            await db.execute("ALTER TABLE notification_log ADD COLUMN channel TEXT NOT NULL DEFAULT 'internal'")
            await db.commit()
        if "user_id" not in nl_columns:
            await db.execute("ALTER TABLE notification_log ADD COLUMN user_id INTEGER REFERENCES users(id)")
            await db.commit()
        # Replace old unique (event_type, reference_type, reference_id) with
        # (event_type, reference_type, reference_id, channel).
        await db.execute("DROP INDEX IF EXISTS idx_notification_dedup")
        await db.execute(
            "CREATE UNIQUE INDEX idx_notification_dedup "
            "ON notification_log (event_type, reference_type, reference_id, channel)"
        )
        await db.commit()
