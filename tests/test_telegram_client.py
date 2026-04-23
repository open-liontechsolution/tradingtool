"""Tests for backend.telegram_client — escaping + no-op behaviour."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_escape_md_escapes_all_specials():
    from backend.telegram_client import escape_md

    out = escape_md("a.b-c_d+e=f*g|h(i)j[k]l{m}n!o#p>q~r`s")
    # Every special character must be preceded by a backslash
    for ch in "._-+=*|()[]{}!#>~`":
        assert f"\\{ch}" in out, f"missing escape for {ch}"


def test_escape_md_leaves_plain_text_untouched():
    from backend.telegram_client import escape_md

    assert escape_md("hello world 42") == "hello world 42"


def test_escape_md_handles_none():
    from backend.telegram_client import escape_md

    assert escape_md(None) == ""


def test_escape_md_coerces_non_strings():
    from backend.telegram_client import escape_md

    assert escape_md(3.14) == "3\\.14"


@pytest.mark.asyncio
async def test_send_message_is_noop_when_token_missing(monkeypatch):
    """With TELEGRAM_BOT_TOKEN empty, no HTTP call must be made."""
    monkeypatch.setattr("backend.telegram_client.TELEGRAM_BOT_TOKEN", "")

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock()

    with patch("backend.telegram_client.httpx.AsyncClient", return_value=mock_client):
        from backend.telegram_client import send_message

        ok = await send_message(chat_id=12345, text="hi")

    assert ok is False
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_posts_when_token_present(monkeypatch):
    monkeypatch.setattr("backend.telegram_client.TELEGRAM_BOT_TOKEN", "fake-token")

    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"ok": True, "result": {}})

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=resp)

    with patch("backend.telegram_client.httpx.AsyncClient", return_value=mock_client):
        from backend.telegram_client import send_message

        ok = await send_message(chat_id=12345, text="hi")

    assert ok is True
    mock_client.post.assert_called_once()
    # Confirm chat_id and text made it into the payload
    _args, kwargs = mock_client.post.call_args
    assert kwargs["json"]["chat_id"] == 12345
    assert kwargs["json"]["text"] == "hi"


@pytest.mark.asyncio
async def test_send_message_handles_non_ok(monkeypatch):
    monkeypatch.setattr("backend.telegram_client.TELEGRAM_BOT_TOKEN", "fake-token")

    resp = MagicMock()
    resp.status_code = 400
    resp.json = MagicMock(return_value={"ok": False, "description": "bad chat"})

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=resp)

    with patch("backend.telegram_client.httpx.AsyncClient", return_value=mock_client):
        from backend.telegram_client import send_message

        ok = await send_message(chat_id=12345, text="hi")

    assert ok is False
