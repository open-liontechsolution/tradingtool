"""Seeder for slot C — ETHUSDT 4h, full 2022 (bear market).

Cross-pair coverage + bear regime so strategies hit losing streaks and
sustained downtrends. ~2190 candles.

Run from the repo root:

    python -m tests.fixtures.parity._seed_slot_c
"""

from __future__ import annotations

from tests.fixtures.parity._seeder import seed_slot


def main() -> None:
    seed_slot(
        name="slot_c",
        symbol="ETHUSDT",
        interval="4h",
        interval_ms=4 * 60 * 60 * 1000,
        start_ms=1_640_995_200_000,  # 2022-01-01 00:00 UTC
        end_ms=1_672_531_200_000,  # 2023-01-01 00:00 UTC (exclusive)
    )


if __name__ == "__main__":
    main()
