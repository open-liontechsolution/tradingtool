"""Seeder for slot A — BTCUSDT 4h, full 2023 + 2024.

Run from the repo root:

    python -m tests.fixtures.parity._seed_slot_a
"""

from __future__ import annotations

from tests.fixtures.parity._seeder import seed_slot


def main() -> None:
    seed_slot(
        name="slot_a",
        symbol="BTCUSDT",
        interval="4h",
        interval_ms=4 * 60 * 60 * 1000,
        start_ms=1_672_531_200_000,  # 2023-01-01 00:00 UTC
        end_ms=1_735_689_600_000,  # 2025-01-01 00:00 UTC (exclusive)
    )


if __name__ == "__main__":
    main()
