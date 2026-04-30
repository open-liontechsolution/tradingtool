"""REST API routes for signals, SimTrades, RealTrades, and comparison."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from backend.auth import AuthUser, get_current_user
from backend.database import get_db
from backend.rate_limit import limiter

router = APIRouter(tags=["signals"])


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SignalConfigCreate(BaseModel):
    symbol: str
    interval: str
    strategy: str
    params: dict = {}
    initial_portfolio: float = 10_000.0
    invested_amount: float | None = None
    leverage: float | None = None
    cost_bps: float = 10.0
    maintenance_margin_pct: float = 0.005
    polling_interval_s: int | None = None
    telegram_enabled: bool = False
    max_loss_per_trade_enabled: bool = False
    max_loss_per_trade_pct: float = 0.02


class SignalConfigPatch(BaseModel):
    active: bool | None = None
    initial_portfolio: float | None = None
    invested_amount: float | None = None
    leverage: float | None = None
    cost_bps: float | None = None
    maintenance_margin_pct: float | None = None
    polling_interval_s: int | None = None
    telegram_enabled: bool | None = None
    max_loss_per_trade_enabled: bool | None = None
    max_loss_per_trade_pct: float | None = None


class RealTradeCreate(BaseModel):
    sim_trade_id: int | None = None
    signal_id: int | None = None
    symbol: str
    side: str
    entry_price: float
    entry_time: str
    quantity: float
    fees: float = 0.0
    notes: str | None = None


class RealTradePatch(BaseModel):
    exit_price: float | None = None
    exit_time: str | None = None
    fees: float | None = None
    pnl: float | None = None
    notes: str | None = None
    status: str | None = None


# ---------------------------------------------------------------------------
# Signal Configs
# ---------------------------------------------------------------------------


@router.post("/signals/configs")
@limiter.limit("10/minute")
async def create_signal_config(
    request: Request,
    req: SignalConfigCreate,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Create a new signal config."""
    from backend.strategies import get_strategy

    # Validate strategy exists
    try:
        get_strategy(req.strategy)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc

    if req.invested_amount is None and req.leverage is None:
        req.leverage = 1.0

    now = _now_iso()
    params_json = json.dumps(req.params, sort_keys=True)

    async with get_db() as db:
        try:
            cursor = await db.execute(
                """INSERT INTO signal_configs
                    (user_id, symbol, interval, strategy, params,
                     initial_portfolio, current_portfolio,
                     invested_amount, leverage, cost_bps,
                     maintenance_margin_pct,
                     max_loss_per_trade_enabled, max_loss_per_trade_pct,
                     polling_interval_s, active, telegram_enabled, last_processed_candle,
                     created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 0, ?, ?)""",
                (
                    user.id,
                    req.symbol.upper(),
                    req.interval,
                    req.strategy,
                    params_json,
                    req.initial_portfolio,
                    req.initial_portfolio,
                    req.invested_amount,
                    req.leverage,
                    req.cost_bps,
                    req.maintenance_margin_pct,
                    1 if req.max_loss_per_trade_enabled else 0,
                    req.max_loss_per_trade_pct,
                    req.polling_interval_s,
                    1 if req.telegram_enabled else 0,
                    now,
                    now,
                ),
            )
            await db.commit()
            config_id = cursor.lastrowid
        except Exception as exc:
            # SQLite raises `IntegrityError: UNIQUE constraint failed: ...`
            # (uppercase). asyncpg raises `UniqueViolationError` whose
            # str() is `duplicate key value violates unique constraint
            # "..."` (lowercase). Match case-insensitively so both
            # backends end up at the same 409.
            if "unique" in str(exc).lower():
                raise HTTPException(
                    409,
                    "A config with the same symbol/interval/strategy/params already exists",
                ) from exc
            raise

    return {"id": config_id, "status": "created"}


@router.get("/signals/configs")
async def list_signal_configs(
    active_only: bool = Query(False),
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """List signal configs owned by the current user."""
    async with get_db() as db:
        query = "SELECT * FROM signal_configs WHERE user_id = ?"
        params: list[Any] = [user.id]
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY id DESC"
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    configs = []
    for row in rows:
        c = dict(zip(cols, row, strict=False))
        c["params"] = json.loads(c["params"]) if isinstance(c["params"], str) else c["params"]
        c["active"] = bool(c["active"])
        c["telegram_enabled"] = bool(c.get("telegram_enabled"))
        c["max_loss_per_trade_enabled"] = bool(c.get("max_loss_per_trade_enabled"))
        configs.append(c)
    return {"configs": configs}


@router.patch("/signals/configs/{config_id}")
async def patch_signal_config(
    config_id: int,
    req: SignalConfigPatch,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Update a signal config (activate/deactivate, change params)."""
    fields: list[str] = []
    values: list[Any] = []

    if req.active is not None:
        fields.append("active = ?")
        values.append(1 if req.active else 0)
    if req.initial_portfolio is not None:
        # Editing initial_portfolio is a "starting capital" relabel — never
        # touches current_portfolio (which evolves with closed PnL).
        fields.append("initial_portfolio = ?")
        values.append(req.initial_portfolio)
    if req.invested_amount is not None:
        fields.append("invested_amount = ?")
        values.append(req.invested_amount)
    if req.leverage is not None:
        fields.append("leverage = ?")
        values.append(req.leverage)
    if req.cost_bps is not None:
        fields.append("cost_bps = ?")
        values.append(req.cost_bps)
    if req.maintenance_margin_pct is not None:
        fields.append("maintenance_margin_pct = ?")
        values.append(req.maintenance_margin_pct)
    if req.polling_interval_s is not None:
        fields.append("polling_interval_s = ?")
        values.append(req.polling_interval_s)
    if req.telegram_enabled is not None:
        fields.append("telegram_enabled = ?")
        values.append(1 if req.telegram_enabled else 0)
    if req.max_loss_per_trade_enabled is not None:
        fields.append("max_loss_per_trade_enabled = ?")
        values.append(1 if req.max_loss_per_trade_enabled else 0)
    if req.max_loss_per_trade_pct is not None:
        fields.append("max_loss_per_trade_pct = ?")
        values.append(req.max_loss_per_trade_pct)

    if not fields:
        raise HTTPException(400, "No fields to update")

    fields.append("updated_at = ?")
    values.append(_now_iso())
    values.append(config_id)
    values.append(user.id)

    async with get_db() as db:
        # SAFE: `fields` entries are appended above from a closed set of
        # literals derived from the SignalConfigPatch Pydantic model fields,
        # not from user input. Values bind through the placeholders.
        cursor = await db.execute(
            f"UPDATE signal_configs SET {', '.join(fields)} WHERE id = ? AND user_id = ?",
            values,
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Config {config_id} not found")

    return {"id": config_id, "status": "updated"}


@router.post("/signals/configs/{config_id}/reset-equity")
@limiter.limit("10/minute")
async def reset_signal_config_equity(
    request: Request,
    config_id: int,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Reset a blown config back to the initial equity (#50).

    Restores ``current_portfolio = initial_portfolio``, clears ``blown_at``,
    flips ``status`` back to ``'active'``. Sim_trade history is *not* touched
    — old closed trades stay in DB so the user can audit what happened. The
    next signal sizes against the fresh equity.
    """
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT initial_portfolio, status FROM signal_configs WHERE id = ? AND user_id = ?",
            (config_id, user.id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(404, f"Config {config_id} not found")

        initial = float(row[0])
        await db.execute(
            "UPDATE signal_configs SET current_portfolio = ?, status = 'active', "
            "blown_at = NULL, updated_at = ? WHERE id = ?",
            (initial, now, config_id),
        )
        await db.commit()

    return {
        "id": config_id,
        "status": "active",
        "current_portfolio": initial,
    }


@router.delete("/signals/configs/{config_id}")
async def delete_signal_config(
    config_id: int,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Delete a signal config. Closes any open SimTrades for it."""
    now = _now_iso()
    async with get_db() as db:
        # Verify ownership
        cursor = await db.execute(
            "SELECT id FROM signal_configs WHERE id = ? AND user_id = ?",
            (config_id, user.id),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(404, f"Config {config_id} not found")

        # Close open sim trades
        await db.execute(
            """UPDATE sim_trades SET status = 'closed', exit_reason = 'config_deleted',
                  updated_at = ?
               WHERE config_id = ? AND status IN ('pending_entry', 'open')""",
            (now, config_id),
        )
        await db.execute(
            """UPDATE signals SET status = 'closed'
               WHERE config_id = ? AND status IN ('pending', 'active')""",
            (config_id,),
        )
        await db.execute(
            "DELETE FROM signal_configs WHERE id = ?",
            (config_id,),
        )
        await db.commit()
    return {"id": config_id, "status": "deleted"}


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


@router.get("/signals")
async def list_signals(
    config_id: int | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, le=500),
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """List generated signals owned by the current user."""
    query = """
        SELECT s.*,
               st.id       AS sim_trade_id,
               st.entry_price,
               st.entry_time,
               st.status   AS sim_trade_status,
               sc.params   AS config_params
        FROM signals s
        LEFT JOIN sim_trades st ON st.signal_id = s.id
        JOIN signal_configs sc ON s.config_id = sc.id
        WHERE sc.user_id = ?
    """
    params: list[Any] = [user.id]
    if config_id is not None:
        query += " AND s.config_id = ?"
        params.append(config_id)
    if status is not None:
        query += " AND s.status = ?"
        params.append(status)
    query += f" ORDER BY s.id DESC LIMIT {limit}"

    async with get_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    return {"signals": [dict(zip(cols, row, strict=False)) for row in rows]}


@router.get("/signals/status")
async def signals_status(user: AuthUser = Depends(get_current_user)) -> dict:
    """Status overview for the current user's trades and configs."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signal_configs WHERE active = 1 AND user_id = ?",
            (user.id,),
        )
        active_configs = (await cursor.fetchone())[0]

        cursor = await db.execute(
            """SELECT COUNT(*) FROM sim_trades st
               JOIN signal_configs sc ON st.config_id = sc.id
               WHERE st.status = 'open' AND sc.user_id = ?""",
            (user.id,),
        )
        open_trades = (await cursor.fetchone())[0]

        cursor = await db.execute(
            """SELECT COUNT(*) FROM sim_trades st
               JOIN signal_configs sc ON st.config_id = sc.id
               WHERE st.status = 'pending_entry' AND sc.user_id = ?""",
            (user.id,),
        )
        pending_trades = (await cursor.fetchone())[0]

        cutoff_24h = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        cursor = await db.execute(
            """SELECT COUNT(*) FROM signals s
               JOIN signal_configs sc ON s.config_id = sc.id
               WHERE s.created_at > ? AND sc.user_id = ?""",
            (cutoff_24h, user.id),
        )
        recent_signals = (await cursor.fetchone())[0]

    return {
        "active_configs": active_configs,
        "open_sim_trades": open_trades,
        "pending_sim_trades": pending_trades,
        "signals_last_24h": recent_signals,
    }


@router.get("/signals/{signal_id}")
async def get_signal(
    signal_id: int,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Get a single signal by ID (verifies ownership)."""
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT s.* FROM signals s
               JOIN signal_configs sc ON s.config_id = sc.id
               WHERE s.id = ? AND sc.user_id = ?""",
            (signal_id, user.id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(404, f"Signal {signal_id} not found")
        cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row, strict=False))


# ---------------------------------------------------------------------------
# SimTrades
# ---------------------------------------------------------------------------


@router.get("/sim-trades")
async def list_sim_trades(
    config_id: int | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, le=500),
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """List SimTrades owned by the current user."""
    query = """
        SELECT st.*, sc.strategy AS config_strategy, sc.params AS config_params
        FROM sim_trades st
        JOIN signal_configs sc ON st.config_id = sc.id
        WHERE sc.user_id = ?
    """
    params: list[Any] = [user.id]
    if config_id is not None:
        query += " AND st.config_id = ?"
        params.append(config_id)
    if status is not None:
        query += " AND st.status = ?"
        params.append(status)
    query += f" ORDER BY st.id DESC LIMIT {limit}"

    async with get_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    return {"sim_trades": [dict(zip(cols, row, strict=False)) for row in rows]}


@router.get("/sim-trades/{trade_id}")
async def get_sim_trade(
    trade_id: int,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Get a single SimTrade by ID (verifies ownership)."""
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT st.* FROM sim_trades st
               JOIN signal_configs sc ON st.config_id = sc.id
               WHERE st.id = ? AND sc.user_id = ?""",
            (trade_id, user.id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(404, f"SimTrade {trade_id} not found")
        cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row, strict=False))


@router.get("/sim-trades/{trade_id}/stop-moves")
async def list_sim_trade_stop_moves(
    trade_id: int,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Return the trailing-stop movement history of a single SimTrade."""
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT st.id FROM sim_trades st
               JOIN signal_configs sc ON st.config_id = sc.id
               WHERE st.id = ? AND sc.user_id = ?""",
            (trade_id, user.id),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(404, f"SimTrade {trade_id} not found")

        cursor = await db.execute(
            """SELECT id, sim_trade_id, prev_stop_base, new_stop_base,
                      candle_time, created_at
               FROM sim_trade_stop_moves
               WHERE sim_trade_id = ?
               ORDER BY id ASC""",
            (trade_id,),
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    return {"stop_moves": [dict(zip(cols, row, strict=False)) for row in rows]}


@router.post("/sim-trades/{trade_id}/close")
@limiter.limit("30/minute")
async def close_sim_trade(
    request: Request,
    trade_id: int,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Manually close an open SimTrade at current market price."""
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT st.* FROM sim_trades st
               JOIN signal_configs sc ON st.config_id = sc.id
               WHERE st.id = ? AND st.status = 'open' AND sc.user_id = ?""",
            (trade_id, user.id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(404, "SimTrade not found or not open")
        cols = [d[0] for d in cursor.description]
    trade = dict(zip(cols, row, strict=False))

    from backend.binance_client import binance_client

    try:
        current_price = await binance_client.get_ticker_price(trade["symbol"])
    except Exception as exc:
        raise HTTPException(503, f"Could not fetch price: {exc}") from exc

    entry_price = float(trade["entry_price"])
    quantity = float(trade["quantity"])

    async with get_db() as db:
        cursor2 = await db.execute(
            "SELECT cost_bps FROM signal_configs WHERE id = ?",
            (trade["config_id"],),
        )
        cfg_row = await cursor2.fetchone()
    cost_factor = float(cfg_row[0]) / 10_000.0 if cfg_row else 0.0

    if trade["side"] == "long":
        gross_pnl = quantity * (current_price - entry_price)
    else:
        gross_pnl = quantity * (entry_price - current_price)
    exit_fee = abs(quantity * current_price) * cost_factor
    net_pnl = gross_pnl - exit_fee
    total_fees = float(trade["fees"] or 0) + exit_fee
    portfolio = float(trade["portfolio"])
    pnl_pct = net_pnl / portfolio if portfolio > 0 else 0.0
    now = _now_iso()
    now_ms = int(datetime.now(UTC).timestamp() * 1000)

    async with get_db() as db:
        await db.execute(
            """UPDATE sim_trades
               SET exit_price = ?, exit_time = ?, exit_reason = 'manual',
                   status = 'closed', pnl = ?, pnl_pct = ?, fees = ?,
                   updated_at = ?
               WHERE id = ?""",
            (current_price, now_ms, net_pnl, pnl_pct, total_fees, now, trade_id),
        )
        await db.execute(
            "UPDATE signals SET status = 'closed' WHERE id = ?",
            (trade["signal_id"],),
        )
        # Apply PnL to the config's current_portfolio (#48). Mirror live_tracker.
        await db.execute(
            "UPDATE signal_configs SET current_portfolio = current_portfolio + ?, updated_at = ? WHERE id = ?",
            (net_pnl, now, trade["config_id"]),
        )
        await db.commit()

    # Trigger blown-state transition if equity dropped to ≤0 (#50).
    from backend.live_tracker import _maybe_mark_blown  # noqa: PLC0415

    await _maybe_mark_blown(trade["config_id"], now)

    return {
        "id": trade_id,
        "status": "closed",
        "exit_price": current_price,
        "pnl": round(net_pnl, 4),
        "pnl_pct": round(pnl_pct, 6),
    }


# ---------------------------------------------------------------------------
# Real Trades
# ---------------------------------------------------------------------------


@router.post("/real-trades")
@limiter.limit("10/minute")
async def create_real_trade(
    request: Request,
    req: RealTradeCreate,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Register a real trade, optionally linked to a SimTrade/Signal."""
    now = _now_iso()
    async with get_db() as db:
        # Verify ownership of linked sim_trade or signal if provided
        if req.sim_trade_id is not None:
            cursor = await db.execute(
                """SELECT st.id FROM sim_trades st
                   JOIN signal_configs sc ON st.config_id = sc.id
                   WHERE st.id = ? AND sc.user_id = ?""",
                (req.sim_trade_id, user.id),
            )
            if await cursor.fetchone() is None:
                raise HTTPException(404, "Linked SimTrade not found")
        if req.signal_id is not None:
            cursor = await db.execute(
                """SELECT s.id FROM signals s
                   JOIN signal_configs sc ON s.config_id = sc.id
                   WHERE s.id = ? AND sc.user_id = ?""",
                (req.signal_id, user.id),
            )
            if await cursor.fetchone() is None:
                raise HTTPException(404, "Linked Signal not found")

        cursor = await db.execute(
            """INSERT INTO real_trades
                (sim_trade_id, signal_id, symbol, side, entry_price, entry_time,
                 quantity, fees, notes, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
            (
                req.sim_trade_id,
                req.signal_id,
                req.symbol.upper(),
                req.side,
                req.entry_price,
                req.entry_time,
                req.quantity,
                req.fees,
                req.notes,
                now,
                now,
            ),
        )
        await db.commit()
        trade_id = cursor.lastrowid
    return {"id": trade_id, "status": "created"}


@router.get("/real-trades")
async def list_real_trades(
    sim_trade_id: int | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, le=500),
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """List real trades owned by the current user."""
    query = """
        SELECT rt.* FROM real_trades rt
        LEFT JOIN sim_trades st ON rt.sim_trade_id = st.id
        LEFT JOIN signals sg ON rt.signal_id = sg.id
        LEFT JOIN signal_configs sc ON COALESCE(st.config_id, sg.config_id) = sc.id
        WHERE sc.user_id = ?
    """
    params: list[Any] = [user.id]
    if sim_trade_id is not None:
        query += " AND rt.sim_trade_id = ?"
        params.append(sim_trade_id)
    if status is not None:
        query += " AND rt.status = ?"
        params.append(status)
    query += f" ORDER BY rt.id DESC LIMIT {limit}"

    async with get_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    return {"real_trades": [dict(zip(cols, row, strict=False)) for row in rows]}


@router.patch("/real-trades/{trade_id}")
@limiter.limit("30/minute")
async def patch_real_trade(
    request: Request,
    trade_id: int,
    req: RealTradePatch,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Update a real trade (close it, add notes, etc.)."""
    # Verify ownership
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT rt.id FROM real_trades rt
               LEFT JOIN sim_trades st ON rt.sim_trade_id = st.id
               LEFT JOIN signals sg ON rt.signal_id = sg.id
               LEFT JOIN signal_configs sc ON COALESCE(st.config_id, sg.config_id) = sc.id
               WHERE rt.id = ? AND sc.user_id = ?""",
            (trade_id, user.id),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(404, f"RealTrade {trade_id} not found")

    fields: list[str] = []
    values: list[Any] = []

    if req.exit_price is not None:
        fields.append("exit_price = ?")
        values.append(req.exit_price)
    if req.exit_time is not None:
        fields.append("exit_time = ?")
        values.append(req.exit_time)
    if req.fees is not None:
        fields.append("fees = ?")
        values.append(req.fees)
    if req.pnl is not None:
        fields.append("pnl = ?")
        values.append(req.pnl)
    if req.notes is not None:
        fields.append("notes = ?")
        values.append(req.notes)
    if req.status is not None:
        fields.append("status = ?")
        values.append(req.status)

    if not fields:
        raise HTTPException(400, "No fields to update")

    # Auto-compute pnl_pct if pnl provided
    if req.pnl is not None:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT entry_price, quantity FROM real_trades WHERE id = ?",
                (trade_id,),
            )
            row = await cursor.fetchone()
        if row:
            invested = float(row[0]) * float(row[1])
            if invested > 0:
                fields.append("pnl_pct = ?")
                values.append(req.pnl / invested)

    fields.append("updated_at = ?")
    values.append(_now_iso())
    values.append(trade_id)

    async with get_db() as db:
        # SAFE: `fields` entries are appended above from a closed set of
        # literals derived from the RealTradePatch Pydantic model fields,
        # not from user input. Values bind through the placeholders.
        cursor = await db.execute(
            f"UPDATE real_trades SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"RealTrade {trade_id} not found")

    return {"id": trade_id, "status": "updated"}


@router.delete("/real-trades/{trade_id}")
async def delete_real_trade(
    trade_id: int,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Delete a real trade (verifies ownership)."""
    async with get_db() as db:
        # Verify ownership
        cursor = await db.execute(
            """SELECT rt.id FROM real_trades rt
               LEFT JOIN sim_trades st ON rt.sim_trade_id = st.id
               LEFT JOIN signals sg ON rt.signal_id = sg.id
               LEFT JOIN signal_configs sc ON COALESCE(st.config_id, sg.config_id) = sc.id
               WHERE rt.id = ? AND sc.user_id = ?""",
            (trade_id, user.id),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(404, f"RealTrade {trade_id} not found")

        await db.execute(
            "DELETE FROM real_trades WHERE id = ?",
            (trade_id,),
        )
        await db.commit()
    return {"id": trade_id, "status": "deleted"}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@router.get("/comparison/{sim_trade_id}")
async def compare_trades(
    sim_trade_id: int,
    user: AuthUser = Depends(get_current_user),
) -> dict:
    """Compare a SimTrade with its linked RealTrade(s) (verifies ownership)."""
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT st.* FROM sim_trades st
               JOIN signal_configs sc ON st.config_id = sc.id
               WHERE st.id = ? AND sc.user_id = ?""",
            (sim_trade_id, user.id),
        )
        sim_row = await cursor.fetchone()
        if sim_row is None:
            raise HTTPException(404, f"SimTrade {sim_trade_id} not found")
        sim_cols = [d[0] for d in cursor.description]
        sim = dict(zip(sim_cols, sim_row, strict=False))

        cursor2 = await db.execute(
            "SELECT * FROM real_trades WHERE sim_trade_id = ?",
            (sim_trade_id,),
        )
        real_rows = await cursor2.fetchall()
        real_cols = [d[0] for d in cursor2.description]
    reals = [dict(zip(real_cols, row, strict=False)) for row in real_rows]

    comparisons = []
    for real in reals:
        entry_slippage = (float(real["entry_price"]) - float(sim["entry_price"])) if sim["entry_price"] else None
        exit_slippage = None
        if real.get("exit_price") and sim.get("exit_price"):
            exit_slippage = float(real["exit_price"]) - float(sim["exit_price"])
        pnl_diff = None
        if real.get("pnl") is not None and sim.get("pnl") is not None:
            pnl_diff = float(real["pnl"]) - float(sim["pnl"])

        comparisons.append(
            {
                "real_trade": real,
                "entry_slippage": round(entry_slippage, 6) if entry_slippage is not None else None,
                "exit_slippage": round(exit_slippage, 6) if exit_slippage is not None else None,
                "pnl_diff": round(pnl_diff, 4) if pnl_diff is not None else None,
            }
        )

    return {
        "sim_trade": sim,
        "comparisons": comparisons,
    }
