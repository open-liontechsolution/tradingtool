"""Tests for backend.api.telegram_routes — /start <token> → chat_id stored."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import get_db, init_db


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    db_path = str(tmp_path / "test_webhook.db")
    os.environ["DB_PATH"] = db_path
    import backend.database as dbmod

    dbmod.DB_PATH = __import__("pathlib").Path(db_path)
    yield


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _build_app():
    """Build a minimal FastAPI app mounting only the telegram router.

    We avoid importing backend.app so the lifespan (background tasks, Alembic)
    doesn't try to start.
    """
    # Import after monkeypatched config to ensure the router picks it up.
    from backend.api import telegram_routes as tg_routes

    app = FastAPI()
    app.include_router(tg_routes.router, prefix="/api")
    return app


async def _insert_user(username: str = "alice") -> int:
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO users (keycloak_sub, email, username, roles, created_at, last_login_at)
               VALUES (?, ?, ?, '[]', ?, ?)""",
            (f"sub-{username}", f"{username}@x.com", username, now, now),
        )
        await db.commit()
        return cursor.lastrowid


async def _issue_token(user_id: int, *, token: str = "abc123", ttl_minutes: int = 15) -> None:
    from datetime import timedelta

    now = datetime.now(UTC)
    async with get_db() as db:
        await db.execute(
            """INSERT INTO telegram_link_tokens (token, user_id, created_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (token, user_id, now.isoformat(), (now + timedelta(minutes=ttl_minutes)).isoformat()),
        )
        await db.commit()


async def _expire_token(token: str) -> None:
    from datetime import timedelta

    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE telegram_link_tokens SET expires_at = ? WHERE token = ?",
            (past, token),
        )
        await db.commit()


def test_extract_start_token_accepts_plain():
    from backend.api.telegram_routes import _extract_start_token

    assert _extract_start_token("/start tok123") == "tok123"


def test_extract_start_token_accepts_mentioned_bot():
    from backend.api.telegram_routes import _extract_start_token

    assert _extract_start_token("/start@mybot tok123") == "tok123"


def test_extract_start_token_rejects_other_commands():
    from backend.api.telegram_routes import _extract_start_token

    assert _extract_start_token("/help tok") is None
    assert _extract_start_token("hello world") is None
    assert _extract_start_token("/start") is None
    assert _extract_start_token("") is None


@pytest.mark.asyncio
async def test_webhook_requires_secret(monkeypatch):
    monkeypatch.setattr("backend.api.telegram_routes.TELEGRAM_ENABLED", True)
    monkeypatch.setattr("backend.api.telegram_routes.TELEGRAM_WEBHOOK_SECRET", "correct-secret")

    await init_db()
    app = _build_app()
    client = TestClient(app)

    # Wrong secret in path
    resp = client.post(
        "/api/telegram/webhook/wrong",
        json={},
        headers={"X-Telegram-Bot-Api-Secret-Token": "correct-secret"},
    )
    assert resp.status_code == 404

    # Correct path, wrong header
    resp = client.post(
        "/api/telegram/webhook/correct-secret",
        json={"message": {"chat": {"id": 1}, "text": "/start abc"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "nope"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_disabled_returns_404(monkeypatch):
    monkeypatch.setattr("backend.api.telegram_routes.TELEGRAM_ENABLED", False)
    monkeypatch.setattr("backend.api.telegram_routes.TELEGRAM_WEBHOOK_SECRET", "")

    await init_db()
    app = _build_app()
    client = TestClient(app)
    resp = client.post(
        "/api/telegram/webhook/anything",
        json={},
        headers={"X-Telegram-Bot-Api-Secret-Token": "anything"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_start_with_valid_token_links_chat(monkeypatch):
    monkeypatch.setattr("backend.api.telegram_routes.TELEGRAM_ENABLED", True)
    monkeypatch.setattr("backend.api.telegram_routes.TELEGRAM_WEBHOOK_SECRET", "sekret")

    await init_db()
    user_id = await _insert_user()
    await _issue_token(user_id, token="tok_valid")

    app = _build_app()
    with patch(
        "backend.api.telegram_routes.send_message",
        new=AsyncMock(return_value=True),
    ) as mock_send:
        client = TestClient(app)
        resp = client.post(
            "/api/telegram/webhook/sekret",
            json={
                "message": {
                    "chat": {"id": 98765},
                    "from": {"username": "alice_tg"},
                    "text": "/start tok_valid",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "sekret"},
        )

    assert resp.status_code == 200
    mock_send.assert_called()

    # DB side effects: chat_id stored, token marked used.
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT telegram_chat_id, telegram_username FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    assert row[0] == 98765
    assert row[1] == "alice_tg"

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT used_at FROM telegram_link_tokens WHERE token = ?",
            ("tok_valid",),
        )
        used_row = await cursor.fetchone()
    assert used_row[0] is not None


@pytest.mark.asyncio
async def test_start_with_expired_token_rejects(monkeypatch):
    monkeypatch.setattr("backend.api.telegram_routes.TELEGRAM_ENABLED", True)
    monkeypatch.setattr("backend.api.telegram_routes.TELEGRAM_WEBHOOK_SECRET", "sekret")

    await init_db()
    user_id = await _insert_user()
    await _issue_token(user_id, token="tok_old")
    await _expire_token("tok_old")

    app = _build_app()
    with patch(
        "backend.api.telegram_routes.send_message",
        new=AsyncMock(return_value=True),
    ) as mock_send:
        client = TestClient(app)
        resp = client.post(
            "/api/telegram/webhook/sekret",
            json={
                "message": {
                    "chat": {"id": 11111},
                    "from": {"username": "x"},
                    "text": "/start tok_old",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "sekret"},
        )

    assert resp.status_code == 200
    # Chat must NOT be linked
    async with get_db() as db:
        cursor = await db.execute("SELECT telegram_chat_id FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
    assert row[0] is None
    # The user still received an explanatory reply
    mock_send.assert_called()


@pytest.mark.asyncio
async def test_unknown_text_gets_friendly_reply(monkeypatch):
    monkeypatch.setattr("backend.api.telegram_routes.TELEGRAM_ENABLED", True)
    monkeypatch.setattr("backend.api.telegram_routes.TELEGRAM_WEBHOOK_SECRET", "sekret")

    await init_db()
    app = _build_app()
    with patch(
        "backend.api.telegram_routes.send_message",
        new=AsyncMock(return_value=True),
    ) as mock_send:
        client = TestClient(app)
        resp = client.post(
            "/api/telegram/webhook/sekret",
            json={"message": {"chat": {"id": 77}, "from": {}, "text": "hello?"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "sekret"},
        )

    assert resp.status_code == 200
    mock_send.assert_called_once()
