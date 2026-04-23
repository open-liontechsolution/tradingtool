"""Telegram webhook endpoint.

Exposed *without* Keycloak auth because Telegram itself calls it. Two layers of
authentication protect the endpoint:

1. The URL path embeds ``TELEGRAM_WEBHOOK_SECRET`` — only someone who knows the
   secret can hit the route.
2. Telegram also echoes the same secret back in the
   ``X-Telegram-Bot-Api-Secret-Token`` header (passed to ``setWebhook``), which
   we verify on every request.

The only user-facing command we handle is ``/start <token>``. All other
messages are politely acknowledged so the user knows the bot is alive.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from backend.config import TELEGRAM_ENABLED, TELEGRAM_WEBHOOK_SECRET
from backend.database import get_db
from backend.telegram_client import send_message

logger = logging.getLogger(__name__)

router = APIRouter(tags=["telegram"])


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _handle_start(chat_id: int, telegram_username: str | None, token: str) -> str:
    """Consume a link token and bind the chat to the owning user.

    Returns the message text to send back to the user.
    """
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT user_id, expires_at, used_at
               FROM telegram_link_tokens WHERE token = ?""",
            (token,),
        )
        row = await cursor.fetchone()

        if row is None:
            return "❌ Código de vinculación no válido\\. Genera uno nuevo desde tu perfil\\."

        user_id = row[0]
        expires_at = row[1]
        used_at = row[2]

        if used_at is not None:
            return "⚠️ Este código ya fue usado\\. Genera uno nuevo si necesitas re\\-vincular\\."
        if expires_at < now:
            return "⏰ El código ha caducado\\. Genera uno nuevo desde tu perfil\\."

        # Make sure no other user already owns this chat_id. If one does,
        # reject — the user must unlink the other account first.
        cursor = await db.execute(
            "SELECT id FROM users WHERE telegram_chat_id = ? AND id != ?",
            (chat_id, user_id),
        )
        if await cursor.fetchone() is not None:
            return "⚠️ Este chat ya está vinculado a otra cuenta\\."

        # Bind. Clear the telegram_* fields first in case the user was
        # already linked to a different chat.
        await db.execute(
            """UPDATE users
               SET telegram_chat_id = ?,
                   telegram_username = ?,
                   telegram_linked_at = ?
               WHERE id = ?""",
            (chat_id, telegram_username, now, user_id),
        )
        await db.execute(
            "UPDATE telegram_link_tokens SET used_at = ? WHERE token = ?",
            (now, token),
        )
        await db.commit()

    return "✅ Vinculación completada\\. Ahora recibirás aquí las alertas de las configuraciones que tengas con Telegram activado\\."


def _extract_start_token(text: str) -> str | None:
    """Return the token from ``/start <token>`` or ``/start@bot <token>``."""
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].split("@", 1)[0]
    if cmd != "/start":
        return None
    if len(parts) < 2:
        return None
    token = parts[1].strip()
    return token or None


@router.post("/telegram/webhook/{secret}")
async def telegram_webhook(
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    """Receive updates from Telegram Bot API and process ``/start <token>``."""
    if not TELEGRAM_ENABLED or not TELEGRAM_WEBHOOK_SECRET:
        # When disabled we return 404 to hide the endpoint's existence.
        raise HTTPException(404, "Not Found")

    if secret != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(404, "Not Found")
    if x_telegram_bot_api_secret_token != TELEGRAM_WEBHOOK_SECRET:
        logger.warning("Telegram webhook: missing/wrong X-Telegram-Bot-Api-Secret-Token header")
        raise HTTPException(401, "Unauthorized")

    try:
        update: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body") from None

    message = update.get("message") or update.get("edited_message")
    if not message:
        # Non-message update (e.g. edited_message with attachment). Ignore.
        return {"ok": True}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return {"ok": True}

    from_user = message.get("from") or {}
    telegram_username = from_user.get("username")  # may be None

    text = message.get("text", "")
    token = _extract_start_token(text)
    if token is None:
        # Friendly reply so the user knows the bot is alive, but do nothing.
        await send_message(
            chat_id,
            (
                "👋 Hola, soy el bot de Trading Tools\\.\n"
                "Para vincular este chat con tu cuenta, abre tu perfil en la app "
                "y pulsa *Vincular Telegram* — te daré un enlace\\."
            ),
        )
        return {"ok": True}

    reply = await _handle_start(chat_id=chat_id, telegram_username=telegram_username, token=token)
    await send_message(chat_id, reply)
    return {"ok": True}
