"""API round-trip tests for the max-loss-per-trade fields on /api/signals/configs (#142).

The engine-level tests in ``test_live_integration.py`` and ``test_backtest_engine.py``
exercise the filter against direct ``signal_configs`` rows. These tests cover the
HTTP/Pydantic/INSERT/PATCH/GET layer specifically — the path a real frontend takes —
so a typo in the SQL placeholder list, a missing PATCH branch, or a forgotten
serializer cast would surface here instead of being caught only by manual QA.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import AuthUser, get_current_user


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    db_path = str(tmp_path / "test_max_loss_api.db")
    os.environ["DB_PATH"] = db_path
    import backend.database as dbmod
    from backend.rate_limit import limiter

    dbmod.DB_PATH = Path(db_path)
    # Same rationale as test_authz.py: TestClient shares a single client IP so
    # the per-route rate-limit bucket fills across tests in the same module.
    prev_enabled = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = prev_enabled


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _build_app(user: AuthUser) -> FastAPI:
    from backend.api import signal_routes

    app = FastAPI()
    app.include_router(signal_routes.router, prefix="/api")

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    return app


async def _init_schema_and_user() -> AuthUser:
    from backend.database import get_db, init_db

    await init_db()

    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO users (keycloak_sub, email, username, roles, created_at, last_login_at) "
            "VALUES (?, ?, ?, '[]', ?, ?)",
            ("sub-test", "test@x.com", "test", _now_iso(), _now_iso()),
        )
        user_id = cur.lastrowid
        await db.commit()
    return AuthUser(id=user_id, keycloak_sub="sub-test", email="test@x.com", username="test", roles=[])


def _base_payload(**overrides) -> dict:
    payload = {
        "symbol": "BTCUSDT",
        "interval": "1d",
        "strategy": "breakout",
        "params": {"N_entrada": 20, "M_salida": 10, "stop_pct": 0.02},
        "initial_portfolio": 1000,
        "leverage": 1,
        "cost_bps": 0,
    }
    payload.update(overrides)
    return payload


def _fetch_config(client: TestClient, config_id: int) -> dict:
    resp = client.get("/api/signals/configs")
    assert resp.status_code == 200
    matches = [c for c in resp.json()["configs"] if c["id"] == config_id]
    assert len(matches) == 1, f"config {config_id} not found in list"
    return matches[0]


# ---------------------------------------------------------------------------
# POST: defaults
# ---------------------------------------------------------------------------


def test_create_without_max_loss_uses_defaults():
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    resp = client.post("/api/signals/configs", json=_base_payload())
    assert resp.status_code == 200, resp.text
    config_id = resp.json()["id"]

    config = _fetch_config(client, config_id)
    assert config["max_loss_per_trade_enabled"] is False
    assert config["max_loss_per_trade_pct"] == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# POST: explicit values round-trip
# ---------------------------------------------------------------------------


def test_create_with_max_loss_enabled_round_trips():
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    resp = client.post(
        "/api/signals/configs",
        json=_base_payload(
            max_loss_per_trade_enabled=True,
            max_loss_per_trade_pct=0.015,
        ),
    )
    assert resp.status_code == 200, resp.text
    config_id = resp.json()["id"]

    config = _fetch_config(client, config_id)
    assert config["max_loss_per_trade_enabled"] is True
    assert config["max_loss_per_trade_pct"] == pytest.approx(0.015)


def test_create_with_max_loss_disabled_persists_pct_for_later_use():
    """User can pre-fill the threshold even with the toggle off — a later PATCH
    can flip the toggle without re-sending the value."""
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    resp = client.post(
        "/api/signals/configs",
        json=_base_payload(
            max_loss_per_trade_enabled=False,
            max_loss_per_trade_pct=0.05,
        ),
    )
    assert resp.status_code == 200
    config = _fetch_config(client, resp.json()["id"])
    assert config["max_loss_per_trade_enabled"] is False
    assert config["max_loss_per_trade_pct"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# PATCH: each field independently
# ---------------------------------------------------------------------------


def test_patch_enables_max_loss():
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    create = client.post("/api/signals/configs", json=_base_payload())
    config_id = create.json()["id"]

    resp = client.patch(
        f"/api/signals/configs/{config_id}",
        json={"max_loss_per_trade_enabled": True},
    )
    assert resp.status_code == 200, resp.text

    config = _fetch_config(client, config_id)
    assert config["max_loss_per_trade_enabled"] is True
    # pct was not patched — must keep the create-time default
    assert config["max_loss_per_trade_pct"] == pytest.approx(0.02)


def test_patch_updates_max_loss_pct_alone():
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    create = client.post(
        "/api/signals/configs",
        json=_base_payload(max_loss_per_trade_enabled=True, max_loss_per_trade_pct=0.02),
    )
    config_id = create.json()["id"]

    resp = client.patch(
        f"/api/signals/configs/{config_id}",
        json={"max_loss_per_trade_pct": 0.005},
    )
    assert resp.status_code == 200, resp.text

    config = _fetch_config(client, config_id)
    # The PATCH only sent pct — the toggle must remain True from create-time
    assert config["max_loss_per_trade_enabled"] is True
    assert config["max_loss_per_trade_pct"] == pytest.approx(0.005)


def test_patch_disables_max_loss_keeps_pct():
    """Disabling the toggle does NOT reset the pct: the user can re-enable it later
    without re-sending the value."""
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    create = client.post(
        "/api/signals/configs",
        json=_base_payload(max_loss_per_trade_enabled=True, max_loss_per_trade_pct=0.03),
    )
    config_id = create.json()["id"]

    resp = client.patch(
        f"/api/signals/configs/{config_id}",
        json={"max_loss_per_trade_enabled": False},
    )
    assert resp.status_code == 200, resp.text

    config = _fetch_config(client, config_id)
    assert config["max_loss_per_trade_enabled"] is False
    assert config["max_loss_per_trade_pct"] == pytest.approx(0.03)


def test_patch_both_fields_at_once():
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    create = client.post("/api/signals/configs", json=_base_payload())
    config_id = create.json()["id"]

    resp = client.patch(
        f"/api/signals/configs/{config_id}",
        json={
            "max_loss_per_trade_enabled": True,
            "max_loss_per_trade_pct": 0.025,
        },
    )
    assert resp.status_code == 200, resp.text

    config = _fetch_config(client, config_id)
    assert config["max_loss_per_trade_enabled"] is True
    assert config["max_loss_per_trade_pct"] == pytest.approx(0.025)


# ---------------------------------------------------------------------------
# PATCH: regression — patching unrelated fields doesn't clobber max_loss
# ---------------------------------------------------------------------------


def test_patch_unrelated_field_preserves_max_loss():
    """A PATCH that only changes ``active`` must not zero out the max-loss columns.
    Catches the failure mode of a PATCH builder that emits ``UPDATE … SET col=NULL``
    for fields the request didn't mention.
    """
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    create = client.post(
        "/api/signals/configs",
        json=_base_payload(max_loss_per_trade_enabled=True, max_loss_per_trade_pct=0.01),
    )
    config_id = create.json()["id"]

    resp = client.patch(f"/api/signals/configs/{config_id}", json={"active": False})
    assert resp.status_code == 200

    config = _fetch_config(client, config_id)
    assert config["active"] is False
    # max-loss state must survive the unrelated patch
    assert config["max_loss_per_trade_enabled"] is True
    assert config["max_loss_per_trade_pct"] == pytest.approx(0.01)
