"""Derived metrics engine: computes technical indicators and saves to derived_metrics table."""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
import aiosqlite

from backend.database import get_db

logger = logging.getLogger(__name__)


async def load_candles_df(
    symbol: str, interval: str, start_ms: int | None = None, end_ms: int | None = None
) -> pd.DataFrame:
    """Load klines from DB into a pandas DataFrame."""
    async with get_db() as db:
        query = "SELECT open_time, open, high, low, close, volume FROM klines WHERE symbol=? AND interval=?"
        params: list[Any] = [symbol, interval]
        if start_ms is not None:
            query += " AND open_time>=?"
            params.append(start_ms)
        if end_ms is not None:
            query += " AND open_time<?"
            params.append(end_ms)
        query += " ORDER BY open_time ASC"
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows,
        columns=["open_time", "open", "high", "low", "close", "volume"],
    )
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["open_time"] = df["open_time"].astype(int)
    return df


async def _upsert_metrics(
    db: aiosqlite.Connection,
    symbol: str,
    interval: str,
    records: list[tuple[int, str, float | None]],
) -> None:
    """Bulk upsert (open_time, metric_name, value) tuples."""
    if not records:
        return
    await db.executemany(
        """
        INSERT OR REPLACE INTO derived_metrics (symbol, interval, open_time, metric_name, value)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(symbol, interval, ot, name, val) for ot, name, val in records],
    )
    await db.commit()


def _series_to_records(
    open_times: pd.Series, name: str, values: pd.Series
) -> list[tuple[int, str, float | None]]:
    return [
        (int(ot), name, None if pd.isna(v) else float(v))
        for ot, v in zip(open_times, values)
    ]


def compute_metrics(df: pd.DataFrame, selected: list[str] | None = None) -> dict[str, pd.Series]:
    """
    Compute requested metrics. If selected is None, compute all.
    Returns a dict of metric_name -> Series aligned with df index.
    """
    if df.empty:
        return {}

    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]

    results: dict[str, pd.Series] = {}

    def want(name: str) -> bool:
        return selected is None or name in selected or any(name.startswith(s.split("_")[0] + "_") for s in (selected or []))

    # Returns
    log_ret = np.log(close / close.shift(1))
    simple_ret = close.pct_change()
    results["returns_log"] = log_ret
    results["returns_simple"] = simple_ret

    # Range
    results["range"] = high - low
    results["true_range"] = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # SMAs
    for n in [20, 50, 200]:
        results[f"sma_{n}"] = close.rolling(n).mean()

    # EMAs
    for n in [20, 50, 200]:
        results[f"ema_{n}"] = close.ewm(span=n, adjust=False).mean()

    # Volatility
    for n in [20, 50]:
        results[f"volatility_{n}"] = log_ret.rolling(n).std()

    # ATR
    for n in [14, 20]:
        results[f"atr_{n}"] = results["true_range"].rolling(n).mean()

    # Rolling max/min
    for n in [20, 50]:
        results[f"rolling_max_{n}"] = high.rolling(n).max()
        results[f"rolling_min_{n}"] = low.rolling(n).min()

    # Donchian channels
    for n in [20, 50]:
        results[f"donchian_upper_{n}"] = high.rolling(n).max()
        results[f"donchian_lower_{n}"] = low.rolling(n).min()

    if selected:
        return {k: v for k, v in results.items() if k in selected}
    return results


async def compute_and_store_metrics(
    symbol: str,
    interval: str,
    selected: list[str] | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict:
    """Load candles, compute metrics, and store in derived_metrics table."""
    df = await load_candles_df(symbol, interval, start_ms, end_ms)
    if df.empty:
        return {"status": "no_data", "metrics_computed": 0, "rows": 0}

    metrics = compute_metrics(df, selected)

    async with get_db() as db:
        total = 0
        for metric_name, series in metrics.items():
            records = _series_to_records(df["open_time"], metric_name, series)
            await _upsert_metrics(db, symbol, interval, records)
            total += len(records)

    return {
        "status": "ok",
        "metrics_computed": len(metrics),
        "rows": total,
        "metric_names": list(metrics.keys()),
    }
