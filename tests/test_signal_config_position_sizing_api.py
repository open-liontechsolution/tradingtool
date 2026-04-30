"""API round-trip tests for ``position_sizing_mode`` on /api/signals/configs (#144).

Engine-level tests in ``test_signal_engine.py`` and ``test_backtest_engine.py``
cover the math; these tests cover the HTTP/Pydantic/INSERT/PATCH/GET layer for
the new sizing-mode field — same surface and pattern as
``test_signal_config_max_loss_api.py`` (#142).
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
    db_path = str(tmp_path / "test_position_sizing_api.db")
    os.environ["DB_PATH"] = db_path
    import backend.database as dbmod
    from backend.rate_limit import limiter

    dbmod.DB_PATH = Path(db_path)
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


def test_create_default_is_full_equity():
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    resp = client.post("/api/signals/configs", json=_base_payload())
    assert resp.status_code == 200, resp.text
    config = _fetch_config(client, resp.json()["id"])
    assert config["position_sizing_mode"] == "full_equity"


def test_create_with_risk_based_round_trips():
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    resp = client.post(
        "/api/signals/configs",
        json=_base_payload(position_sizing_mode="risk_based"),
    )
    assert resp.status_code == 200, resp.text
    config = _fetch_config(client, resp.json()["id"])
    assert config["position_sizing_mode"] == "risk_based"


def test_create_rejects_unknown_mode():
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    resp = client.post(
        "/api/signals/configs",
        json=_base_payload(position_sizing_mode="kelly"),
    )
    # Pydantic Literal validation → 422.
    assert resp.status_code == 422


def test_patch_switches_mode_to_risk_based():
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    create = client.post("/api/signals/configs", json=_base_payload())
    config_id = create.json()["id"]

    resp = client.patch(
        f"/api/signals/configs/{config_id}",
        json={"position_sizing_mode": "risk_based"},
    )
    assert resp.status_code == 200, resp.text
    config = _fetch_config(client, config_id)
    assert config["position_sizing_mode"] == "risk_based"


def test_patch_switches_back_to_full_equity():
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    create = client.post(
        "/api/signals/configs",
        json=_base_payload(position_sizing_mode="risk_based"),
    )
    config_id = create.json()["id"]

    resp = client.patch(
        f"/api/signals/configs/{config_id}",
        json={"position_sizing_mode": "full_equity"},
    )
    assert resp.status_code == 200, resp.text
    config = _fetch_config(client, config_id)
    assert config["position_sizing_mode"] == "full_equity"


def test_patch_unrelated_field_preserves_mode():
    """A PATCH that only changes ``active`` must not reset position_sizing_mode."""
    user = asyncio.run(_init_schema_and_user())
    client = TestClient(_build_app(user))

    create = client.post(
        "/api/signals/configs",
        json=_base_payload(position_sizing_mode="risk_based"),
    )
    config_id = create.json()["id"]

    resp = client.patch(f"/api/signals/configs/{config_id}", json={"active": False})
    assert resp.status_code == 200
    config = _fetch_config(client, config_id)
    assert config["active"] is False
    assert config["position_sizing_mode"] == "risk_based"
