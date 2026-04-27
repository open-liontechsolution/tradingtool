"""Cross-user authorization tests (#63 Sprint 2).

Verifies that user A cannot read, modify, or delete resources owned by user B
through any of the endpoints exposed by ``backend.api.signal_routes``. Each
test inserts both users, creates a resource as A, then drives the API as B
and asserts the resource is invisible / untouchable.

We don't test ``profile_routes`` here because every endpoint there is keyed
by the authenticated user (``user.id``) — there is no resource ID to attempt
to cross — so the cross-user shape is structurally absent.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import AuthUser, get_current_user


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    db_path = str(tmp_path / "test_authz.db")
    os.environ["DB_PATH"] = db_path
    import backend.database as dbmod
    from backend.rate_limit import limiter

    dbmod.DB_PATH = Path(db_path)
    # Disable rate limiting for the duration of the test. The per-route caps
    # added in #83 (e.g. POST /signals/configs at 10/minute) are keyed by
    # client IP — TestClient defaults to a single host ("testclient") so the
    # bucket fills across tests. Restore on teardown so other test modules
    # see the production-default state.
    prev_enabled = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = prev_enabled


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _build_app(user: AuthUser) -> FastAPI:
    """Mount only signal_routes and stub get_current_user to return ``user``."""
    from backend.api import signal_routes

    app = FastAPI()
    app.include_router(signal_routes.router, prefix="/api")

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    return app


async def _init_schema_and_users() -> tuple[AuthUser, AuthUser]:
    from backend.database import get_db, init_db

    await init_db()

    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO users (keycloak_sub, email, username, roles, created_at, last_login_at) "
            "VALUES (?, ?, ?, '[]', ?, ?)",
            ("sub-alice", "alice@x.com", "alice", _now_iso(), _now_iso()),
        )
        alice_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO users (keycloak_sub, email, username, roles, created_at, last_login_at) "
            "VALUES (?, ?, ?, '[]', ?, ?)",
            ("sub-bob", "bob@x.com", "bob", _now_iso(), _now_iso()),
        )
        bob_id = cur.lastrowid
        await db.commit()

    alice = AuthUser(id=alice_id, keycloak_sub="sub-alice", email="alice@x.com", username="alice", roles=[])
    bob = AuthUser(id=bob_id, keycloak_sub="sub-bob", email="bob@x.com", username="bob", roles=[])
    return alice, bob


def _create_config(client: TestClient, *, symbol: str = "BTCUSDT") -> int:
    payload = {
        "symbol": symbol,
        "interval": "1d",
        "strategy": "breakout",
        "params": {"lookback": 20, "stop_pct": 2.0},
        "initial_portfolio": 1000,
        "leverage": 1,
        "cost_bps": 0,
    }
    resp = client.post("/api/signals/configs", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _insert_sim_trade(config_id: int) -> int:
    """Engines normally create sim_trades; for the authz test we insert directly.

    Bypasses the FK to signals by creating a stub signal first.
    """
    from backend.database import get_db

    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO signals (config_id, symbol, interval, strategy, side, "
            " trigger_candle_time, stop_price, status, created_at) "
            "VALUES (?, 'BTCUSDT', '1d', 'breakout', 'long', 1735689600000, 49000, 'pending', ?)",
            (config_id, _now_iso()),
        )
        signal_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO sim_trades "
            "(signal_id, config_id, symbol, interval, side, entry_price, entry_time, "
            " stop_base, status, portfolio, invested_amount, leverage, created_at, updated_at) "
            "VALUES (?, ?, 'BTCUSDT', '1d', 'long', 50000, 1735689600000, "
            " 49000, 'open', 1000, 1000, 1, ?, ?)",
            (signal_id, config_id, _now_iso(), _now_iso()),
        )
        await db.commit()
        return cur.lastrowid


async def _insert_real_trade(*, sim_trade_id: int) -> int:
    """real_trades is normally created via POST; insert directly for the test."""
    from backend.database import get_db

    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO real_trades "
            "(sim_trade_id, signal_id, symbol, side, entry_price, entry_time, "
            " quantity, fees, status, created_at, updated_at) "
            "VALUES (?, NULL, 'BTCUSDT', 'long', 50000, '2026-01-01T00:00:00+00:00', "
            " 0.01, 0, 'open', ?, ?)",
            (sim_trade_id, _now_iso(), _now_iso()),
        )
        await db.commit()
        return cur.lastrowid


# ---------------------------------------------------------------------------
# signal_configs
# ---------------------------------------------------------------------------


def test_user_b_does_not_see_user_a_configs_in_list():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.get("/api/signals/configs")
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()["configs"]]
    assert config_id not in ids


def test_user_b_cannot_patch_user_a_config():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.patch(f"/api/signals/configs/{config_id}", json={"active": False})
    assert resp.status_code == 404


def test_user_b_cannot_delete_user_a_config():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.delete(f"/api/signals/configs/{config_id}")
    assert resp.status_code == 404

    # Resource should still exist for Alice
    resp = alice_client.get("/api/signals/configs")
    assert config_id in [c["id"] for c in resp.json()["configs"]]


def test_user_b_cannot_reset_equity_of_user_a_config():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.post(f"/api/signals/configs/{config_id}/reset-equity")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# sim_trades
# ---------------------------------------------------------------------------


def test_user_b_does_not_see_user_a_sim_trades_in_list():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)
    trade_id = asyncio.run(_insert_sim_trade(config_id))

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.get("/api/sim-trades")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()["sim_trades"]]
    assert trade_id not in ids


def test_user_b_cannot_get_user_a_sim_trade():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)
    trade_id = asyncio.run(_insert_sim_trade(config_id))

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.get(f"/api/sim-trades/{trade_id}")
    assert resp.status_code == 404


def test_user_b_cannot_close_user_a_sim_trade():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)
    trade_id = asyncio.run(_insert_sim_trade(config_id))

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.post(f"/api/sim-trades/{trade_id}/close")
    assert resp.status_code == 404


def test_user_b_cannot_see_stop_moves_of_user_a_sim_trade():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)
    trade_id = asyncio.run(_insert_sim_trade(config_id))

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.get(f"/api/sim-trades/{trade_id}/stop-moves")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# real_trades
# ---------------------------------------------------------------------------


def test_user_b_does_not_see_user_a_real_trades_in_list():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)
    sim_id = asyncio.run(_insert_sim_trade(config_id))
    real_id = asyncio.run(_insert_real_trade(sim_trade_id=sim_id))

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.get("/api/real-trades")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()["real_trades"]]
    assert real_id not in ids


def test_user_b_cannot_patch_user_a_real_trade():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)
    sim_id = asyncio.run(_insert_sim_trade(config_id))
    real_id = asyncio.run(_insert_real_trade(sim_trade_id=sim_id))

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.patch(f"/api/real-trades/{real_id}", json={"notes": "hijacked"})
    assert resp.status_code == 404


def test_user_b_cannot_delete_user_a_real_trade():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)
    sim_id = asyncio.run(_insert_sim_trade(config_id))
    real_id = asyncio.run(_insert_real_trade(sim_trade_id=sim_id))

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.delete(f"/api/real-trades/{real_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------


def test_user_b_does_not_see_user_a_signals_in_list():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)

    # Insert a signal directly tied to Alice's config
    async def _insert_signal() -> int:
        from backend.database import get_db

        async with get_db() as db:
            cur = await db.execute(
                "INSERT INTO signals (config_id, symbol, interval, strategy, side, "
                " trigger_candle_time, stop_price, status, created_at) "
                "VALUES (?, 'BTCUSDT', '1d', 'breakout', 'long', 1735689600000, 49000, "
                " 'pending', ?)",
                (config_id, _now_iso()),
            )
            await db.commit()
            return cur.lastrowid

    signal_id = asyncio.run(_insert_signal())

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.get("/api/signals")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()["signals"]]
    assert signal_id not in ids


def test_user_b_cannot_get_user_a_signal():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_id = _create_config(alice_client)

    async def _insert_signal() -> int:
        from backend.database import get_db

        async with get_db() as db:
            cur = await db.execute(
                "INSERT INTO signals (config_id, symbol, interval, strategy, side, "
                " trigger_candle_time, stop_price, status, created_at) "
                "VALUES (?, 'BTCUSDT', '1d', 'breakout', 'long', 1735689600000, 49000, "
                " 'pending', ?)",
                (config_id, _now_iso()),
            )
            await db.commit()
            return cur.lastrowid

    signal_id = asyncio.run(_insert_signal())

    bob_client = TestClient(_build_app(bob))
    resp = bob_client.get(f"/api/signals/{signal_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# status counters do not leak across users
# ---------------------------------------------------------------------------


def test_status_counters_are_per_user():
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    _create_config(alice_client)
    _create_config(alice_client, symbol="ETHUSDT")

    bob_client = TestClient(_build_app(bob))
    _create_config(bob_client)

    resp = alice_client.get("/api/signals/status")
    assert resp.json()["active_configs"] == 2

    resp = bob_client.get("/api/signals/status")
    assert resp.json()["active_configs"] == 1


def test_same_strategy_params_dont_collide_across_users():
    """Regression: idx_signal_configs_unique used to ignore user_id, so
    Alice's config blocked Bob from creating the same symbol/interval/
    strategy/params (and leaked her config's existence via 409). With the
    per-user unique index (#63 Sprint 2) both users can run the same
    setup independently."""
    alice, bob = asyncio.run(_init_schema_and_users())

    alice_client = TestClient(_build_app(alice))
    config_a = _create_config(alice_client, symbol="BTCUSDT")

    bob_client = TestClient(_build_app(bob))
    # Same symbol/interval/strategy/params as Alice — must succeed now.
    config_b = _create_config(bob_client, symbol="BTCUSDT")

    assert config_a != config_b

    # And each user only sees their own.
    a_ids = [c["id"] for c in alice_client.get("/api/signals/configs").json()["configs"]]
    b_ids = [c["id"] for c in bob_client.get("/api/signals/configs").json()["configs"]]
    assert config_a in a_ids and config_a not in b_ids
    assert config_b in b_ids and config_b not in a_ids


# ---------------------------------------------------------------------------
# Duplicate-config error mapping (post-#111 follow-up)
# ---------------------------------------------------------------------------


def test_same_user_duplicate_payload_returns_409_sqlite():
    """SQLite path: a user creating the same symbol/interval/strategy/params
    twice should hit the per-user unique index and get a clean 409, not a
    raw 500 from the DB layer. SQLite's IntegrityError says
    'UNIQUE constraint failed' (uppercase)."""
    alice, _ = asyncio.run(_init_schema_and_users())

    client = TestClient(_build_app(alice))
    payload = {
        "symbol": "BTCUSDT",
        "interval": "1d",
        "strategy": "breakout",
        "params": {"lookback": 20, "stop_pct": 2.0},
        "initial_portfolio": 1000,
        "leverage": 1,
        "cost_bps": 0,
    }
    first = client.post("/api/signals/configs", json=payload)
    assert first.status_code == 200, first.text

    second = client.post("/api/signals/configs", json=payload)
    assert second.status_code == 409, second.text
    assert "already exists" in second.json()["detail"].lower()


def test_same_user_duplicate_payload_returns_409_postgres_style(monkeypatch):
    """Postgres path: asyncpg raises UniqueViolationError whose str() is
    `duplicate key value violates unique constraint "..."` — note the
    LOWERCASE 'unique'. The original handler did `if "UNIQUE" in str(exc)`
    (case-sensitive) so on Postgres a duplicate slipped through and
    surfaced as 500. Simulate the asyncpg-style exception string and
    assert the handler still returns 409."""
    alice, _ = asyncio.run(_init_schema_and_users())

    client = TestClient(_build_app(alice))
    # First insert succeeds normally.
    payload = {
        "symbol": "ETHUSDT",
        "interval": "4h",
        "strategy": "breakout",
        "params": {"lookback": 30},
        "initial_portfolio": 1000,
        "leverage": 1,
        "cost_bps": 0,
    }
    first = client.post("/api/signals/configs", json=payload)
    assert first.status_code == 200, first.text

    # Second insert: monkey-patch the db connection's execute to raise an
    # asyncpg-style UniqueViolationError-shaped exception (without depending
    # on asyncpg being installed in the test env).
    import backend.api.signal_routes as routes_module  # noqa: PLC0415

    original_get_db = routes_module.get_db

    class _PgStyleError(Exception):
        pass

    @asynccontextmanager
    async def _failing_get_db():
        async with original_get_db() as db:
            real_execute = db.execute

            async def _raising_execute(query: str, params=()):
                if "INSERT INTO signal_configs" in query:
                    raise _PgStyleError(
                        'duplicate key value violates unique constraint "idx_signal_configs_user_unique"'
                    )
                return await real_execute(query, params)

            db.execute = _raising_execute  # type: ignore[method-assign]
            yield db

    monkeypatch.setattr(routes_module, "get_db", _failing_get_db)

    second = client.post("/api/signals/configs", json=payload)
    assert second.status_code == 409, second.text
    assert "already exists" in second.json()["detail"].lower()
