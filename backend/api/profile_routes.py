"""User profile routes — Telegram linking lives here.

Flow (see ``telegram_routes.py`` for the webhook side):

1. UI calls ``POST /api/profile/telegram/link-token`` → backend returns an
   opaque token + the deep-link ``https://t.me/<bot>?start=<token>``.
2. User opens the link on Telegram and sends ``/start <token>`` to the bot.
3. Telegram POSTs the update to our webhook, which marks the token as used
   and stores ``telegram_chat_id`` / ``telegram_username`` on the user row.
4. UI polls ``GET /api/profile/telegram`` until ``linked=true``.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from backend.auth import AuthUser, get_current_user
from backend.config import TELEGRAM_BOT_USERNAME, TELEGRAM_ENABLED
from backend.database import get_db

router = APIRouter(tags=["profile"])

_LINK_TOKEN_TTL = timedelta(minutes=15)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@router.get("/profile/telegram")
async def get_telegram_status(user: AuthUser = Depends(get_current_user)) -> dict:
    """Return the current user's Telegram link status."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT telegram_chat_id, telegram_username, telegram_linked_at FROM users WHERE id = ?",
            (user.id,),
        )
        row = await cursor.fetchone()

    chat_id = row[0] if row else None
    username = row[1] if row else None
    linked_at = row[2] if row else None

    return {
        "linked": chat_id is not None,
        "telegram_username": username,
        "linked_at": linked_at,
        "bot_configured": TELEGRAM_ENABLED,
        "bot_username": TELEGRAM_BOT_USERNAME or None,
    }


@router.post("/profile/telegram/link-token")
async def create_link_token(user: AuthUser = Depends(get_current_user)) -> dict:
    """Generate a one-time token the user can send to the bot as ``/start <token>``."""
    if not TELEGRAM_ENABLED:
        raise HTTPException(
            503,
            "Telegram bot is not configured on this server",
        )
    if not TELEGRAM_BOT_USERNAME:
        # Without a username the deep-link cannot be built.
        raise HTTPException(
            503,
            "Telegram bot username is not configured on this server",
        )

    token = secrets.token_urlsafe(18)
    now = datetime.now(UTC)
    expires = now + _LINK_TOKEN_TTL

    async with get_db() as db:
        # Purge any previous unused tokens for this user so only the latest is valid.
        await db.execute(
            "DELETE FROM telegram_link_tokens WHERE user_id = ? AND used_at IS NULL",
            (user.id,),
        )
        await db.execute(
            """INSERT INTO telegram_link_tokens
                (token, user_id, created_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (token, user.id, now.isoformat(), expires.isoformat()),
        )
        await db.commit()

    return {
        "token": token,
        "expires_at": expires.isoformat(),
        "deep_link": f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={token}",
        "bot_username": TELEGRAM_BOT_USERNAME,
    }


@router.delete("/profile/telegram")
async def unlink_telegram(user: AuthUser = Depends(get_current_user)) -> dict:
    """Unlink the user's Telegram chat and invalidate any pending link tokens."""
    async with get_db() as db:
        await db.execute(
            """UPDATE users
               SET telegram_chat_id = NULL,
                   telegram_username = NULL,
                   telegram_linked_at = NULL
               WHERE id = ?""",
            (user.id,),
        )
        await db.execute(
            "DELETE FROM telegram_link_tokens WHERE user_id = ?",
            (user.id,),
        )
        await db.commit()
    return {"linked": False}
