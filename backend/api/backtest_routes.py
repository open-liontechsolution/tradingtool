"""REST API routes for backtesting."""
from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.strategies import list_strategies
from backend.backtest_engine import run_backtest

router = APIRouter(tags=["backtest"])

# In-memory store for backtest results (keyed by UUID string)
_results: dict[str, dict] = {}


class BacktestRequest(BaseModel):
    symbol: str
    interval: str
    start_time: int   # ms timestamp
    end_time: int     # ms timestamp
    strategy: str
    params: dict = {}
    initial_capital: float = 10_000.0


@router.get("/strategies")
async def get_strategies() -> dict:
    """List available strategies with parameter definitions."""
    return {"strategies": list_strategies()}


@router.post("/backtest")
async def start_backtest(req: BacktestRequest) -> dict:
    """Run a backtest and store results, returning a result ID."""
    if req.end_time <= req.start_time:
        raise HTTPException(400, "end_time must be > start_time")
    if req.initial_capital <= 0:
        raise HTTPException(400, "initial_capital must be positive")

    result = await run_backtest(
        symbol=req.symbol.upper(),
        interval=req.interval,
        start_ms=req.start_time,
        end_ms=req.end_time,
        strategy_name=req.strategy,
        params=req.params,
        initial_capital=req.initial_capital,
    )

    if result.error:
        raise HTTPException(422, result.error)

    backtest_id = str(uuid.uuid4())
    payload = {
        "id": backtest_id,
        "symbol": req.symbol.upper(),
        "interval": req.interval,
        "strategy": req.strategy,
        "params": req.params,
        "initial_capital": req.initial_capital,
        "equity_curve": result.equity_curve,
        "trade_log": result.trade_log,
        "summary": result.summary,
        "liquidated": result.liquidated,
    }
    _results[backtest_id] = payload
    return {
        "id": backtest_id,
        "summary": result.summary,
        "liquidated": result.liquidated,
        "n_trades": len(result.trade_log),
    }


@router.get("/backtest/{backtest_id}")
async def get_backtest(backtest_id: str) -> dict:
    """Get full backtest results by ID."""
    result = _results.get(backtest_id)
    if result is None:
        raise HTTPException(404, f"Backtest {backtest_id} not found")
    return result


@router.get("/backtest/{backtest_id}/export")
async def export_backtest(
    backtest_id: str,
    format: str = Query("json", pattern="^(json|csv)$"),
) -> StreamingResponse:
    """Export backtest trade log as JSON or CSV."""
    result = _results.get(backtest_id)
    if result is None:
        raise HTTPException(404, f"Backtest {backtest_id} not found")

    trade_log: list[dict] = result.get("trade_log", [])

    if format == "json":
        content = json.dumps({"trade_log": trade_log, "summary": result.get("summary", {})}, indent=2)
        return StreamingResponse(
            io.StringIO(content),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="backtest_{backtest_id}.json"'},
        )

    # CSV
    if not trade_log:
        content = "No trades\n"
    else:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(trade_log[0].keys()))
        writer.writeheader()
        writer.writerows(trade_log)
        content = output.getvalue()

    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="backtest_{backtest_id}.csv"'},
    )
