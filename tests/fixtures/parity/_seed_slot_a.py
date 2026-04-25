"""One-shot seeder for the parity-test fixture (slot A: BTCUSDT 4h, 2023-2024).

Run from the repo root:

    python -m tests.fixtures.parity._seed_slot_a

Writes ``slot_a.json.gz`` next to this file. The output is committed to the repo
so the parity harness never hits the network at test time.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import httpx

SYMBOL = "BTCUSDT"
INTERVAL = "4h"
START_MS = 1_672_531_200_000  # 2023-01-01 00:00 UTC
END_MS = 1_735_689_600_000  # 2025-01-01 00:00 UTC (exclusive)
STEP_MS = 4 * 60 * 60 * 1000
BATCH = 1000

OUT = Path(__file__).resolve().parent / "slot_a.json.gz"


def fetch_batch(client: httpx.Client, start_ms: int, end_ms: int, limit: int) -> list[list]:
    resp = client.get(
        "https://api.binance.com/api/v3/klines",
        params={
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "startTime": start_ms,
            "endTime": end_ms - 1,
            "limit": limit,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    rows: list[dict] = []
    cursor = START_MS
    with httpx.Client() as client:
        while cursor < END_MS:
            batch_end = min(cursor + BATCH * STEP_MS, END_MS)
            raw = fetch_batch(client, cursor, batch_end, BATCH)
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
            last_open = int(raw[-1][0])
            cursor = last_open + STEP_MS
            time.sleep(0.2)

    rows.sort(key=lambda r: r["open_time"])
    seen = set()
    deduped = []
    for r in rows:
        if r["open_time"] in seen:
            continue
        seen.add(r["open_time"])
        deduped.append(r)

    payload = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "start_ms": START_MS,
        "end_ms": END_MS,
        "step_ms": STEP_MS,
        "candles": deduped,
    }
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"wrote {len(deduped)} candles → {OUT} ({OUT.stat().st_size / 1024:.1f} KiB)")


if __name__ == "__main__":
    main()
