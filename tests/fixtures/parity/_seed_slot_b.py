"""Seeder for slot B — BTCUSDT 1h, 2024 Q1.

High-density slot: ~2160 candles in 90 days. Useful to exercise trailing
move_stop with frequent zigzag points and gappy intrabar moves.

Run from the repo root:

    python -m tests.fixtures.parity._seed_slot_b
"""

from __future__ import annotations

from tests.fixtures.parity._seeder import seed_slot


def main() -> None:
    seed_slot(
        name="slot_b",
        symbol="BTCUSDT",
        interval="1h",
        interval_ms=60 * 60 * 1000,
        start_ms=1_704_067_200_000,  # 2024-01-01 00:00 UTC
        end_ms=1_711_929_600_000,  # 2024-04-01 00:00 UTC (exclusive)
    )


if __name__ == "__main__":
    main()
