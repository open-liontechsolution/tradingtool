"""Tests for backend.notifications — dispatch filtering, dedup, formatting."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from backend.database import get_db, init_db


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    db_path = str(tmp_path / "test_notif.db")
    os.environ["DB_PATH"] = db_path
    import backend.database as dbmod

    dbmod.DB_PATH = __import__("pathlib").Path(db_path)
    yield


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _insert_user(*, chat_id: int | None = None, username: str | None = None) -> int:
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO users (keycloak_sub, email, username, roles, created_at, last_login_at,
                                  telegram_chat_id, telegram_username, telegram_linked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"test-{username or 'u'}-{chat_id or 0}",
                f"{username or 'u'}@example.com",
                username or "u",
                "[]",
                now,
                now,
                chat_id,
                username,
                now if chat_id else None,
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def _insert_config(user_id: int, *, telegram_enabled: bool = False) -> int:
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO signal_configs
                (user_id, symbol, interval, strategy, params,
                 initial_portfolio, current_portfolio,
                 invested_amount, leverage, cost_bps, polling_interval_s,
                 active, telegram_enabled, last_processed_candle, created_at, updated_at)
               VALUES (?, 'BTCUSDT', '1h', 'breakout', '{}', 10000, 10000,
                       NULL, 1.0, 10.0, NULL,
                       1, ?, 0, ?, ?)""",
            (user_id, 1 if telegram_enabled else 0, now, now),
        )
        await db.commit()
        return cursor.lastrowid


async def _count_log(channel: str) -> int:
    async with get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM notification_log WHERE channel = ?", (channel,))
        row = await cursor.fetchone()
    return row[0]


@pytest.mark.asyncio
async def test_internal_log_always_written_even_without_telegram():
    """The internal log must be written even when Telegram isn't configured."""
    await init_db()
    user_id = await _insert_user(chat_id=None)
    config_id = await _insert_config(user_id, telegram_enabled=False)

    from backend.notifications import notify_event

    with (
        patch("backend.notifications.TELEGRAM_ENABLED", False),
        patch("backend.notifications.send_message", new=AsyncMock(return_value=True)) as mock_send,
    ):
        await notify_event(
            event_type="entry",
            config_id=config_id,
            reference_type="sim_trade",
            reference_id=999,
            payload={
                "symbol": "BTCUSDT",
                "interval": "1h",
                "side": "long",
                "strategy": "breakout",
                "entry_price": 65432.1,
                "stop_price": 63200.0,
                "invested_amount": 1000.0,
                "leverage": 1.0,
                "sim_trade_id": 999,
            },
        )

    assert await _count_log("internal") == 1
    assert await _count_log("telegram") == 0
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_skip_telegram_when_toggle_off():
    """telegram_enabled=false → no Telegram call, even with a linked chat."""
    await init_db()
    user_id = await _insert_user(chat_id=42, username="user42")
    config_id = await _insert_config(user_id, telegram_enabled=False)

    from backend.notifications import notify_event

    with (
        patch("backend.notifications.TELEGRAM_ENABLED", True),
        patch("backend.notifications.send_message", new=AsyncMock(return_value=True)) as mock_send,
    ):
        await notify_event(
            event_type="stop_hit",
            config_id=config_id,
            reference_type="sim_trade",
            reference_id=101,
            payload={
                "symbol": "BTCUSDT",
                "interval": "1h",
                "side": "long",
                "exit_price": 63000.0,
                "pnl": -50.0,
                "pnl_pct": -0.05,
                "exit_reason": "stop_intrabar",
                "sim_trade_id": 101,
            },
        )

    mock_send.assert_not_called()
    assert await _count_log("telegram") == 0


@pytest.mark.asyncio
async def test_skip_telegram_when_no_chat_id():
    """Toggle on but user hasn't linked chat → skip Telegram."""
    await init_db()
    user_id = await _insert_user(chat_id=None)
    config_id = await _insert_config(user_id, telegram_enabled=True)

    from backend.notifications import notify_event

    with (
        patch("backend.notifications.TELEGRAM_ENABLED", True),
        patch("backend.notifications.send_message", new=AsyncMock(return_value=True)) as mock_send,
    ):
        await notify_event(
            event_type="exit_signal",
            config_id=config_id,
            reference_type="sim_trade",
            reference_id=202,
            payload={
                "symbol": "BTCUSDT",
                "interval": "1h",
                "side": "long",
                "exit_price": 70000.0,
                "pnl": 500.0,
                "pnl_pct": 0.05,
                "duration_candles": 12,
                "sim_trade_id": 202,
            },
        )

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_dispatches_to_telegram_when_all_gates_pass():
    """Toggle on + chat linked + bot configured → send_message called once."""
    await init_db()
    user_id = await _insert_user(chat_id=12345, username="linked")
    config_id = await _insert_config(user_id, telegram_enabled=True)

    from backend.notifications import notify_event

    with (
        patch("backend.notifications.TELEGRAM_ENABLED", True),
        patch("backend.notifications.send_message", new=AsyncMock(return_value=True)) as mock_send,
    ):
        await notify_event(
            event_type="entry",
            config_id=config_id,
            reference_type="sim_trade",
            reference_id=303,
            payload={
                "symbol": "BTCUSDT",
                "interval": "1h",
                "side": "long",
                "strategy": "breakout",
                "entry_price": 65432.1,
                "stop_price": 63200.0,
                "invested_amount": 1000.0,
                "leverage": 1.0,
                "sim_trade_id": 303,
            },
        )

    mock_send.assert_called_once()
    chat_arg = mock_send.call_args[0][0]
    text_arg = mock_send.call_args[0][1]
    assert chat_arg == 12345
    assert "Entrada LONG" in text_arg
    assert "BTCUSDT" in text_arg
    assert await _count_log("internal") == 1
    assert await _count_log("telegram") == 1


@pytest.mark.asyncio
async def test_dedup_prevents_double_telegram_send():
    """Calling notify_event twice with the same ref must send only once."""
    await init_db()
    user_id = await _insert_user(chat_id=777)
    config_id = await _insert_config(user_id, telegram_enabled=True)

    payload = {
        "symbol": "BTCUSDT",
        "interval": "1h",
        "side": "long",
        "strategy": "breakout",
        "entry_price": 100.0,
        "stop_price": 98.0,
        "invested_amount": 1000.0,
        "leverage": 1.0,
        "sim_trade_id": 404,
    }

    from backend.notifications import notify_event

    with (
        patch("backend.notifications.TELEGRAM_ENABLED", True),
        patch("backend.notifications.send_message", new=AsyncMock(return_value=True)) as mock_send,
    ):
        await notify_event(
            event_type="entry",
            config_id=config_id,
            reference_type="sim_trade",
            reference_id=404,
            payload=payload,
        )
        await notify_event(
            event_type="entry",
            config_id=config_id,
            reference_type="sim_trade",
            reference_id=404,
            payload=payload,
        )

    assert mock_send.call_count == 1
    assert await _count_log("internal") == 1
    assert await _count_log("telegram") == 1


@pytest.mark.asyncio
async def test_unknown_event_type_is_rejected():
    from backend.notifications import notify_event

    with patch("backend.notifications.send_message", new=AsyncMock(return_value=True)) as mock_send:
        await notify_event(
            event_type="bogus",
            config_id=1,
            reference_type="sim_trade",
            reference_id=1,
            payload={},
        )

    mock_send.assert_not_called()


def test_format_stop_moved_renders_required_fields():
    """The stop_moved formatter produces a message with prev/new stop levels and locked pct."""
    from backend.notifications import _format_stop_moved

    text = _format_stop_moved(
        {
            "symbol": "BTCUSDT",
            "side": "long",
            "interval": "1h",
            "prev_stop": 95.12345,
            "new_stop": 98.50000,
            "locked_pct": 0.015,
            "sim_trade_id": 42,
        }
    )

    assert "Stop movido" in text
    assert "BTCUSDT" in text
    assert "95." in text
    assert "98." in text
    assert "Ganancia bloqueada" in text


def test_format_stop_moved_omits_locked_pct_when_missing():
    from backend.notifications import _format_stop_moved

    text = _format_stop_moved(
        {
            "symbol": "BTCUSDT",
            "side": "short",
            "interval": "4h",
            "prev_stop": 105.0,
            "new_stop": 102.0,
            "sim_trade_id": 7,
        }
    )
    assert "Ganancia bloqueada" not in text


@pytest.mark.asyncio
async def test_stop_moved_event_goes_through_dispatcher():
    """stop_moved is a recognised event: dispatcher writes to the log and sends Telegram."""
    from backend.notifications import notify_event

    await init_db()
    user_id = await _insert_user(chat_id=12345, username="trader")
    config_id = await _insert_config(user_id, telegram_enabled=True)

    with (
        patch("backend.notifications.send_message", new=AsyncMock(return_value=True)) as mock_send,
        patch("backend.notifications.TELEGRAM_ENABLED", True),
    ):
        await notify_event(
            event_type="stop_moved",
            config_id=config_id,
            reference_type="sim_trade_stop_move",
            reference_id=1,
            payload={
                "symbol": "BTCUSDT",
                "side": "long",
                "interval": "1h",
                "prev_stop": 95.0,
                "new_stop": 98.0,
                "locked_pct": 0.02,
                "sim_trade_id": 1,
            },
        )

    assert mock_send.call_count == 1
    assert await _count_log("internal") == 1
    assert await _count_log("telegram") == 1
