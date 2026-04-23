"""Thin async Telegram Bot API client.

Only the two verbs we need: ``send_message`` (to notify users) and
``set_webhook`` (invoked once at startup if a public URL is configured).

Design:
- If ``TELEGRAM_BOT_TOKEN`` is not set, every call becomes a logged no-op.
  This keeps the subsystem fully inert in tests, CI and deploys without a bot.
- Rate-limit aware: on 429 the API returns ``retry_after`` seconds; we respect
  it once and then fail soft (the caller decides whether to retry later).
- All network failures are caught and logged — they must never take down the
  background tracker/scanner loops that invoke this module.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from backend.config import TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_DEFAULT_TIMEOUT = 10.0


def _api_url(method: str) -> str:
    return f"{_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/{method}"


async def _post(method: str, payload: dict[str, Any], timeout: float = _DEFAULT_TIMEOUT) -> dict | None:
    """POST to Telegram Bot API. Returns parsed JSON or None on error."""
    if not TELEGRAM_BOT_TOKEN:
        logger.debug("Telegram not configured (no TELEGRAM_BOT_TOKEN); skipping %s", method)
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_api_url(method), json=payload)
    except httpx.HTTPError as exc:
        logger.warning("Telegram %s network error: %s", method, exc)
        return None

    if resp.status_code == 429:
        # Respect Telegram's suggested retry_after once, then fail soft.
        try:
            retry_after = int(resp.json().get("parameters", {}).get("retry_after", 1))
        except Exception:
            retry_after = 1
        retry_after = min(retry_after, 30)
        logger.info("Telegram %s rate-limited, retrying after %ds", method, retry_after)
        await asyncio.sleep(retry_after)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(_api_url(method), json=payload)
        except httpx.HTTPError as exc:
            logger.warning("Telegram %s retry failed: %s", method, exc)
            return None

    try:
        data = resp.json()
    except Exception:
        logger.warning("Telegram %s returned non-JSON (status=%d)", method, resp.status_code)
        return None

    if not data.get("ok"):
        logger.warning(
            "Telegram %s failed: status=%d description=%r",
            method,
            resp.status_code,
            data.get("description"),
        )
        return None

    return data


async def send_message(
    chat_id: int,
    text: str,
    *,
    parse_mode: str = "MarkdownV2",
    disable_web_page_preview: bool = True,
) -> bool:
    """Send a text message to a chat. Returns True on success."""
    if not chat_id:
        return False
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    result = await _post("sendMessage", payload)
    return result is not None


async def set_webhook(url: str) -> bool:
    """Register the bot's webhook URL. Called once at app startup."""
    payload: dict[str, Any] = {
        "url": url,
        "allowed_updates": ["message"],
    }
    if TELEGRAM_WEBHOOK_SECRET:
        # Telegram echoes this back in X-Telegram-Bot-Api-Secret-Token.
        payload["secret_token"] = TELEGRAM_WEBHOOK_SECRET
    result = await _post("setWebhook", payload)
    return result is not None


# ---------------------------------------------------------------------------
# MarkdownV2 escaping
# ---------------------------------------------------------------------------
#
# The MarkdownV2 spec requires escaping these characters *outside* of code
# blocks and inline code:  _ * [ ] ( ) ~ ` > # + - = | { } . !
# Anything not pre-escaped breaks the whole message parse on Telegram's side.

_MDV2_ESCAPES = r"_*[]()~`>#+-=|{}.!\\"


def escape_md(text: str) -> str:
    """Escape a plain-text string for safe interpolation into MarkdownV2."""
    if text is None:
        return ""
    out: list[str] = []
    for ch in str(text):
        if ch in _MDV2_ESCAPES:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)
