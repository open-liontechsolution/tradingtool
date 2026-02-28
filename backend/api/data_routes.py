"""REST API routes for data management: download, candles, rate limit, metrics."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.binance_client import binance_client
from backend.database import get_db
from backend.download_engine import (
    cancel_job,
    create_download_job,
    get_job,
    start_download_job_task,
)
from backend.metrics_engine import compute_and_store_metrics

router = APIRouter(tags=["data"])

# Common pairs the UI surfaces by default
KNOWN_PAIRS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "DOTUSDT",
    "LINKUSDT",
]

VALID_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"]


class DownloadRequest(BaseModel):
    symbol: str
    interval: str
    start_time: int  # ms timestamp
    end_time: int  # ms timestamp


class MetricsRequest(BaseModel):
    symbol: str
    interval: str
    metrics: list[str] | None = None
    start_time: int | None = None
    end_time: int | None = None


@router.get("/pairs")
async def list_pairs() -> dict:
    """List available pairs (known defaults + ones stored in DB)."""
    async with get_db() as db:
        cursor = await db.execute("SELECT DISTINCT symbol FROM klines ORDER BY symbol")
        rows = await cursor.fetchall()
    stored = [row[0] for row in rows]
    merged = sorted(set(KNOWN_PAIRS) | set(stored))
    return {"pairs": merged}


@router.post("/download")
async def start_download(req: DownloadRequest) -> dict:
    """Start a download job and return its ID."""
    if req.interval not in VALID_INTERVALS:
        raise HTTPException(400, f"Invalid interval: {req.interval}")
    if req.end_time <= req.start_time:
        raise HTTPException(400, "end_time must be > start_time")

    job_id = await create_download_job(req.symbol.upper(), req.interval, req.start_time, req.end_time)
    start_download_job_task(job_id)
    return {"job_id": job_id, "status": "started"}


@router.get("/download/{job_id}")
async def get_download_status(job_id: int) -> dict:
    """Poll job status, progress, and event log."""
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")
    result = dict(job)
    result["log"] = json.loads(result.get("log") or "[]")
    return result


@router.get("/download/{job_id}/cancel")
async def cancel_download(job_id: int) -> dict:
    """Cancel a running or pending download job."""
    ok = await cancel_job(job_id)
    if not ok:
        raise HTTPException(400, "Job not found or already finished")
    return {"job_id": job_id, "status": "cancelled"}


@router.get("/candles")
async def get_candles(
    symbol: str = Query(...),
    interval: str = Query(...),
    start: int | None = Query(None),
    end: int | None = Query(None),
    limit: int = Query(1000, le=10_000),
) -> dict:
    """Query stored candles from DB."""
    async with get_db() as db:
        query = "SELECT * FROM klines WHERE symbol=? AND interval=?"
        params: list[Any] = [symbol.upper(), interval]
        if start is not None:
            query += " AND open_time>=?"
            params.append(start)
        if end is not None:
            query += " AND open_time<?"
            params.append(end)
        query += f" ORDER BY open_time ASC LIMIT {limit}"
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]

    candles = [dict(zip(cols, row, strict=False)) for row in rows]
    return {"candles": candles, "count": len(candles)}


@router.get("/rate-limit")
async def get_rate_limit() -> dict:
    """Current Binance API weight status."""
    return binance_client.rate_limit.to_dict()


@router.post("/metrics/compute")
async def compute_metrics_endpoint(req: MetricsRequest) -> dict:
    """Trigger derived metrics calculation for a symbol/interval."""
    result = await compute_and_store_metrics(
        req.symbol.upper(),
        req.interval,
        req.metrics,
        req.start_time,
        req.end_time,
    )
    return result


@router.get("/coverage")
async def data_coverage() -> dict:
    """Return all symbol/interval combos with candle count and date range."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT symbol, interval, COUNT(*) as count,
                   MIN(open_time) as from_ms, MAX(open_time) as to_ms
            FROM klines
            GROUP BY symbol, interval
            ORDER BY symbol, interval
            """
        )
        rows = await cursor.fetchall()
    return {
        "coverage": [
            {
                "symbol": row[0],
                "interval": row[1],
                "count": row[2],
                "from_ms": row[3],
                "to_ms": row[4],
            }
            for row in rows
        ]
    }


@router.get("/metrics/status")
async def metrics_status(
    symbol: str = Query(...),
    interval: str = Query(...),
) -> dict:
    """Get count of derived metrics stored for a symbol/interval."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT metric_name, COUNT(*) as cnt
            FROM derived_metrics
            WHERE symbol=? AND interval=?
            GROUP BY metric_name
            ORDER BY metric_name
            """,
            (symbol.upper(), interval),
        )
        rows = await cursor.fetchall()
    return {
        "symbol": symbol,
        "interval": interval,
        "metrics": {row[0]: row[1] for row in rows},
    }
