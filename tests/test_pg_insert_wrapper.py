"""Regression tests for `backend.database._PgConnection.execute`.

The Postgres wrapper auto-appends ``RETURNING id`` to every INSERT that lacks
an explicit RETURNING, so callers can read ``cursor.lastrowid`` (aiosqlite
parity). Tables whose PK is not named ``id`` (e.g. ``telegram_link_tokens``)
would otherwise blow up with ``UndefinedColumnError``. These tests pin down
the three relevant behaviours.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import asyncpg
import pytest

from backend.database import _PgConnection


@pytest.mark.asyncio
async def test_execute_insert_auto_appends_returning_id_and_captures_lastrowid():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"id": 42})
    db = _PgConnection(conn)

    cursor = await db.execute("INSERT INTO foo (a, b) VALUES (?, ?)", (1, 2))

    conn.fetchrow.assert_awaited_once()
    sent_query = conn.fetchrow.await_args.args[0]
    assert sent_query.endswith("RETURNING id")
    assert "$1" in sent_query and "$2" in sent_query
    assert cursor.lastrowid == 42
    assert db.lastrowid == 42


@pytest.mark.asyncio
async def test_execute_insert_falls_back_when_table_has_no_id_column():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=asyncpg.exceptions.UndefinedColumnError('column "id" does not exist'))
    conn.execute = AsyncMock(return_value=None)
    db = _PgConnection(conn)

    cursor = await db.execute(
        "INSERT INTO telegram_link_tokens (token, user_id) VALUES (?, ?)",
        ("tok", 7),
    )

    conn.fetchrow.assert_awaited_once()
    conn.execute.assert_awaited_once()
    fallback_query = conn.execute.await_args.args[0]
    assert "RETURNING" not in fallback_query
    assert cursor.lastrowid is None
    assert db.lastrowid is None


@pytest.mark.asyncio
async def test_execute_insert_with_explicit_returning_is_not_rewritten():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    db = _PgConnection(conn)

    await db.execute(
        "INSERT INTO telegram_link_tokens (token, user_id) VALUES (?, ?) RETURNING token",
        ("tok", 7),
    )

    conn.fetch.assert_awaited_once()
    conn.fetchrow.assert_not_called()
    sent_query = conn.fetch.await_args.args[0]
    # The wrapper must leave the caller's RETURNING untouched — no second append.
    assert sent_query.count("RETURNING") == 1
    assert sent_query.rstrip().endswith("RETURNING token")


# ---------------------------------------------------------------------------
# Pool lifecycle helpers (post-#84)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_pg_pool_is_noop_under_sqlite(monkeypatch):
    """SQLite has no pool concept; init_pg_pool must be a silent no-op so
    the SQLite test path (which is the entire CI suite today) doesn't try
    to connect to a Postgres server that isn't there."""
    import backend.database as dbmod

    monkeypatch.setattr(dbmod, "IS_POSTGRES", False)
    monkeypatch.setattr(dbmod, "_pg_pool", None)
    await dbmod.init_pg_pool()
    assert dbmod._pg_pool is None


@pytest.mark.asyncio
async def test_close_pg_pool_is_safe_when_uninitialized(monkeypatch):
    """close_pg_pool must not crash if init_pg_pool was never called or was
    already torn down. Lifespan shutdown can hit this when startup failed."""
    import backend.database as dbmod

    monkeypatch.setattr(dbmod, "_pg_pool", None)
    await dbmod.close_pg_pool()  # must not raise
    assert dbmod._pg_pool is None
