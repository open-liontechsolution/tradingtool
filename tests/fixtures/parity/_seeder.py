"""Shared download helper for parity slot fixtures.

Each slot script (`_seed_slot_a.py`, `_seed_slot_b.py`, ...) just configures
``symbol``, ``interval``, and the date range, then calls ``seed_slot``. The
output is committed to the repo as a gzipped JSON payload that the parity
harness loads directly — tests never hit the network.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import httpx

OUT_DIR = Path(__file__).resolve().parent
BATCH = 1000


def seed_slot(
    *,
    name: str,
    symbol: str,
    interval: str,
    interval_ms: int,
    start_ms: int,
    end_ms: int,
) -> Path:
    """Download klines from Binance public REST and write ``<name>.json.gz``."""
    rows: list[dict] = []
    cursor = start_ms
    with httpx.Client() as client:
        while cursor < end_ms:
            batch_end = min(cursor + BATCH * interval_ms, end_ms)
            resp = client.get(
                "https://api.binance.com/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": batch_end - 1,
                    "limit": BATCH,
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            raw = resp.json()
            if not raw:
                break
            for r in raw:
                rows.append(
                    {
                        "open_time": int(r[0]),
                        "open": str(r[1]),
                        "high": str(r[2]),
                        "low": str(r[3]),
                        "close": str(r[4]),
                        "volume": str(r[5]),
                        "close_time": int(r[6]),
                    }
                )
            cursor = int(raw[-1][0]) + interval_ms
            time.sleep(0.2)

    rows.sort(key=lambda r: r["open_time"])
    seen: set[int] = set()
    deduped: list[dict] = []
    for r in rows:
        if r["open_time"] in seen:
            continue
        seen.add(r["open_time"])
        deduped.append(r)

    payload = {
        "symbol": symbol,
        "interval": interval,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "step_ms": interval_ms,
        "candles": deduped,
    }
    out = OUT_DIR / f"{name}.json.gz"
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"wrote {len(deduped)} candles → {out} ({out.stat().st_size / 1024:.1f} KiB)")
    return out
