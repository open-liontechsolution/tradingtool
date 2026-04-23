"""User-facing notification dispatcher.

Single entry point ``notify_event`` called by the live tracker whenever a
trade event worth surfacing to the user happens (entry filled, exit, stop).

Responsibilities:
1. Resolve the recipient user from the signal_config.
2. Honour the per-config ``telegram_enabled`` toggle.
3. Deduplicate via the UNIQUE(event_type, reference_type, reference_id, channel)
   constraint on ``notification_log``.
4. Format and dispatch the Telegram message.

Event types supported:
    - ``entry``       : position opened (entry price known after fill)
    - ``exit_signal`` : strategy signalled exit on candle close
    - ``stop_hit``    : stop-loss triggered (intrabar or candle-close)
    - ``stop_moved``  : reserved for upcoming trailing-stop support (see
                        trailing-stop GitHub issue). No call-site yet.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from backend.config import PUBLIC_BASE_URL, TELEGRAM_ENABLED
from backend.database import get_db
from backend.telegram_client import escape_md, send_message

logger = logging.getLogger(__name__)

# All event types the dispatcher knows how to format.
_SUPPORTED_EVENTS = {"entry", "exit_signal", "stop_hit", "stop_moved"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _resolve_recipient(config_id: int) -> tuple[int | None, int | None, bool]:
    """Look up (user_id, telegram_chat_id, telegram_enabled) for a signal_config.

    Returns (None, None, False) if the config has no owner — callers treat
    this as "skip Telegram, still log internally".
    """
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT sc.user_id, sc.telegram_enabled, u.telegram_chat_id
               FROM signal_configs sc
               LEFT JOIN users u ON u.id = sc.user_id
               WHERE sc.id = ?""",
            (config_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None, None, False
    user_id = row[0]
    telegram_enabled = bool(row[1])
    chat_id = row[2]
    return user_id, chat_id, telegram_enabled


async def _log_once(
    *,
    event_type: str,
    reference_type: str,
    reference_id: int,
    channel: str,
    user_id: int | None,
    message: str,
) -> bool:
    """Insert a ``notification_log`` row. Returns False if a duplicate existed."""
    try:
        async with get_db() as db:
            await db.execute(
                """INSERT INTO notification_log
                    (event_type, reference_type, reference_id, channel, user_id, message, sent_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (event_type, reference_type, reference_id, channel, user_id, message, _now_iso()),
            )
            await db.commit()
        return True
    except Exception as exc:
        # The most common case is a UNIQUE-constraint violation (dedup) which
        # is expected — the scanner/tracker may evaluate the same event twice
        # across restarts. Log at DEBUG so noise stays out of production.
        logger.debug(
            "notification_log insert skipped (%s/%s/%s/%s): %s",
            event_type,
            reference_type,
            reference_id,
            channel,
            exc,
        )
        return False


# Formatters produce *raw* strings meant to be wrapped in backticks by the
# caller. Inside MarkdownV2 code spans, only `\` and `` ` `` need escaping —
# neither appears in a numeric format — so the output is safe as-is.


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    return f"{value:.4f}"


def _fmt_pct(value: float | None) -> str:
    """Format a decimal ratio (0.0342 → '+3.42%'). Sign always included."""
    if value is None:
        return "—"
    return f"{value * 100.0:+.2f}%"


def _fmt_money(value: float | None, suffix: str = " USDT") -> str:
    if value is None:
        return "—"
    return f"{value:+,.2f}{suffix}"


def _trade_link(sim_trade_id: int | None) -> str | None:
    """Build a MarkdownV2 link to the sim trade in the UI, or None."""
    if not PUBLIC_BASE_URL or sim_trade_id is None:
        return None
    # escape_md on the display text only; the URL must not be escaped.
    url = f"{PUBLIC_BASE_URL}/#sim-trade-{sim_trade_id}"
    return f"[{escape_md('Ver trade ↗')}]({url})"


def _format_entry(payload: dict[str, Any]) -> str:
    leverage_str = escape_md(f"{payload.get('leverage', 1.0):.2f}")
    lines = [
        f"📈 *Entrada {escape_md(payload['side'].upper())}* · `{escape_md(payload['symbol'])}` · {escape_md(payload['interval'])}",
        f"Estrategia: `{escape_md(payload['strategy'])}`",
        f"Precio: `{_fmt_price(payload.get('entry_price'))}`",
        f"Stop: `{_fmt_price(payload.get('stop_price'))}`  \\(auto\\-close `{_fmt_price(payload.get('stop_trigger'))}`\\)",
        f"Invertido: `{_fmt_price(payload.get('invested_amount'))}` USDT  \\(x{leverage_str}\\)",
    ]
    link = _trade_link(payload.get("sim_trade_id"))
    if link:
        lines.append(f"🔗 {link}")
    return "\n".join(lines)


def _format_exit(payload: dict[str, Any]) -> str:
    lines = [
        f"✅ *Salida \\(estrategia\\)* · `{escape_md(payload['symbol'])}` {escape_md(payload['side'])} · {escape_md(payload['interval'])}",
        f"Precio salida: `{_fmt_price(payload.get('exit_price'))}`",
        f"PnL: `{_fmt_pct(payload.get('pnl_pct'))}`  \\({_fmt_money(payload.get('pnl'))}\\)",
    ]
    dur = payload.get("duration_candles")
    if dur is not None:
        lines.append(f"Duración: {escape_md(str(dur))} velas")
    link = _trade_link(payload.get("sim_trade_id"))
    if link:
        lines.append(f"🔗 {link}")
    return "\n".join(lines)


def _format_stop(payload: dict[str, Any]) -> str:
    lines = [
        f"🛑 *Stop alcanzado* · `{escape_md(payload['symbol'])}` {escape_md(payload['side'])} · {escape_md(payload['interval'])}",
        f"Precio salida: `{_fmt_price(payload.get('exit_price'))}`",
        f"PnL: `{_fmt_pct(payload.get('pnl_pct'))}`  \\({_fmt_money(payload.get('pnl'))}\\)",
    ]
    reason = payload.get("exit_reason")
    if reason:
        lines.append(f"Tipo: `{escape_md(reason)}`")
    link = _trade_link(payload.get("sim_trade_id"))
    if link:
        lines.append(f"🔗 {link}")
    return "\n".join(lines)


def _format_stop_moved(payload: dict[str, Any]) -> str:
    """Reserved for trailing-stop; call-site will be added in that feature."""
    lines = [
        f"🎯 *Stop movido* · `{escape_md(payload['symbol'])}` {escape_md(payload['side'])} · {escape_md(payload['interval'])}",
        f"Stop anterior: `{_fmt_price(payload.get('prev_stop'))}`",
        f"Stop nuevo:    `{_fmt_price(payload.get('new_stop'))}`",
    ]
    locked = payload.get("locked_pct")
    if locked is not None:
        lines.append(f"Ganancia bloqueada: `{_fmt_pct(locked)}`")
    link = _trade_link(payload.get("sim_trade_id"))
    if link:
        lines.append(f"🔗 {link}")
    return "\n".join(lines)


_FORMATTERS = {
    "entry": _format_entry,
    "exit_signal": _format_exit,
    "stop_hit": _format_stop,
    "stop_moved": _format_stop_moved,
}


async def notify_event(
    *,
    event_type: str,
    config_id: int,
    reference_type: str,
    reference_id: int,
    payload: dict[str, Any],
) -> None:
    """Dispatch a user-facing notification.

    Always writes to ``notification_log`` (with ``channel='internal'``) so the
    event is auditable even if no Telegram chat is linked. If the owner user
    has a linked chat AND ``signal_configs.telegram_enabled=1``, it also
    sends the Telegram message and logs a second row with ``channel='telegram'``.

    Dedup is per (event_type, reference_type, reference_id, channel), so the
    internal and Telegram rows coexist without colliding.
    """
    if event_type not in _SUPPORTED_EVENTS:
        logger.warning("notify_event: unknown event_type=%r", event_type)
        return

    user_id, chat_id, telegram_enabled = await _resolve_recipient(config_id)

    # Internal log row (always). The UI's notification list reads from here.
    summary = (
        f"{event_type} on {payload.get('symbol', '?')} "
        f"{payload.get('side', '')} @ {payload.get('exit_price') or payload.get('entry_price') or '?'}"
    )
    await _log_once(
        event_type=event_type,
        reference_type=reference_type,
        reference_id=reference_id,
        channel="internal",
        user_id=user_id,
        message=summary,
    )

    # Telegram dispatch, only if all gates pass.
    if not TELEGRAM_ENABLED:
        return
    if not telegram_enabled or not chat_id:
        return

    # Dedup the Telegram side before actually calling the API, so a restart
    # or double-tick doesn't spam the user.
    first_time = await _log_once(
        event_type=event_type,
        reference_type=reference_type,
        reference_id=reference_id,
        channel="telegram",
        user_id=user_id,
        message=summary,
    )
    if not first_time:
        return

    formatter = _FORMATTERS[event_type]
    try:
        text = formatter(payload)
    except Exception as exc:
        logger.error("notify_event: formatter failed for %s: %s", event_type, exc, exc_info=True)
        return

    ok = await send_message(chat_id, text)
    if not ok:
        logger.warning(
            "notify_event: Telegram send failed for user_id=%s event=%s ref=%s/%s",
            user_id,
            event_type,
            reference_type,
            reference_id,
        )
