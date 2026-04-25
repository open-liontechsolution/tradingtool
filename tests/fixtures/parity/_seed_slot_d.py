"""Seeder for slot D — SOLUSDT 15m, 2024 Q2.

Volatile / dense slot for the leveraged-liquidation parity tests (#58 Gap 1).
SOLUSDT in Q2 2024 had wide intrabar swings that exercise the liquidation
check (long-side dips and short-side spikes both occur). ~8640 candles.

Run from the repo root:

    python -m tests.fixtures.parity._seed_slot_d
"""

from __future__ import annotations

from tests.fixtures.parity._seeder import seed_slot


def main() -> None:
    seed_slot(
        name="slot_d",
        symbol="SOLUSDT",
        interval="15m",
        interval_ms=15 * 60 * 1000,
        start_ms=1_711_929_600_000,  # 2024-04-01 00:00 UTC
        end_ms=1_719_792_000_000,  # 2024-07-01 00:00 UTC (exclusive)
    )


if __name__ == "__main__":
    main()
