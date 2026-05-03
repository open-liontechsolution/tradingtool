"""Refresh the cached metrics in ``backend/data/recommendations.yaml``.

For each entry in the catalogue and each requested period (default: 1y, 2y,
3y, 5y) the script:

1. Calls :func:`backend.download_engine.ensure_candles` so the local DB has the
   klines (kicks off a background fetch from Binance if data is missing).
2. Runs :func:`backend.backtest_engine.run_backtest` with ``initial_capital=10_000``
   and the catalogue's ``params`` dict.
3. Reads the resulting summary metrics and rewrites ``metrics_cached`` and
   ``metrics_computed_at`` for the entry.

Composite metric
----------------
``composite = max(0, profit) / max(0.01, abs(dd))`` — a unitless MAR-like ratio
expressing reward per unit drawdown. Single source of truth: :func:`_composite`.
The seeded YAML values are placeholders until this script runs against the
local DB.

CLI
---
::

    python -m backend.scripts.refresh_recommendations_cache --pair BTCUSDT
    python -m backend.scripts.refresh_recommendations_cache --all
    python -m backend.scripts.refresh_recommendations_cache --all --dry-run

The ``refresh`` coroutine is exported so tests can drive it without going
through ``asyncio.run`` and ``argparse``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from backend.backtest_engine import run_backtest
from backend.download_engine import ensure_candles
from backend.recommendations import CATALOG_PATH

logger = logging.getLogger("backend.scripts.refresh_recommendations_cache")

DEFAULT_PERIODS: tuple[str, ...] = ("1y", "2y", "3y", "5y")
DEFAULT_INITIAL_CAPITAL: float = 10_000.0

_PERIOD_DAYS: dict[str, int] = {
    "1y": 365,
    "2y": 730,
    "3y": 1095,
    "5y": 1825,
}


def _composite(profit_fraction: float, dd_fraction: float) -> float:
    """MAR-like ratio: reward per unit drawdown.

    Both inputs are decimal fractions (0.71 for +71%, -0.16 for -16% DD).
    Floor of 0.01 on the denominator avoids divide-by-zero for flat-DD runs.
    """
    return round(max(0.0, profit_fraction) / max(0.01, abs(dd_fraction)), 4)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _period_window_ms(period: str, now_ms: int) -> tuple[int, int]:
    days = _PERIOD_DAYS.get(period)
    if days is None:
        raise ValueError(f"Unknown period: {period!r}. Supported: {sorted(_PERIOD_DAYS)}")
    return now_ms - days * 86_400_000, now_ms


async def _refresh_pair(
    pair: str,
    primary: dict[str, Any],
    periods: Sequence[str],
    initial_capital: float,
    now_ms: int,
) -> dict[str, Any]:
    """Run backtests for one pair across all periods. Returns the new metrics_cached dict."""
    strategy = primary["strategy"]
    interval = primary["timeframe"]
    params = dict(primary.get("params") or {})

    metrics_cached: dict[str, Any] = {}
    for period in periods:
        start_ms, end_ms = _period_window_ms(period, now_ms)
        ready = await ensure_candles(pair, interval, start_ms, end_ms)
        if not ready:
            logger.warning(
                "[%s %s] %s: candles not ready (background sync started). Re-run the script after the sync completes.",
                pair,
                interval,
                period,
            )
        result = await run_backtest(
            symbol=pair,
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
            strategy_name=strategy,
            params=params,
            initial_capital=initial_capital,
        )
        if result.error:
            logger.error("[%s %s] %s: backtest error: %s", pair, interval, period, result.error)
            continue
        summary = result.summary or {}
        # backtest_metrics returns percentages (0-100). The catalogue stores
        # decimal fractions for compactness — convert here so a downstream
        # reader never needs to know which units were used.
        profit = round(float(summary.get("net_profit_pct", 0.0)) / 100.0, 4)
        dd = round(float(summary.get("max_drawdown_pct", 0.0)) / 100.0, 4)
        n_trades = int(summary.get("n_trades", 0))
        metrics_cached[period] = {
            "profit": profit,
            "dd": dd,
            "composite": _composite(profit, dd),
            "n_trades": n_trades,
        }
        logger.info(
            "[%s %s] %s: profit=%+.2f%% dd=%+.2f%% trades=%d composite=%.2f",
            pair,
            interval,
            period,
            profit * 100,
            dd * 100,
            n_trades,
            metrics_cached[period]["composite"],
        )
    return metrics_cached


async def refresh(
    catalog_path: Path | None = None,
    pairs: Sequence[str] | None = None,
    periods: Sequence[str] = DEFAULT_PERIODS,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    dry_run: bool = False,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Refresh metrics_cached for the requested pairs.

    Args:
        catalog_path: defaults to ``backend/data/recommendations.yaml``.
        pairs: subset of pairs to refresh; ``None`` means all entries.
        periods: which windows to recompute; default 1y/2y/3y/5y.
        initial_capital: matches the value the panel shows in the UI.
        dry_run: when True, the YAML is not written back.
        now_ms: lets tests pin the reference time for reproducible windows.

    Returns the parsed catalogue dict (post-refresh in memory, regardless of dry_run).
    """
    target = Path(catalog_path) if catalog_path else CATALOG_PATH
    if not target.exists():
        raise FileNotFoundError(f"Recommendations catalogue not found at {target}")

    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    recs = raw.get("recommendations") or {}
    if not isinstance(recs, dict):
        raise ValueError(f"{target}: 'recommendations' must be a mapping")

    selected_keys: list[str]
    if pairs:
        wanted = {p.upper() for p in pairs}
        selected_keys = [k for k in recs if str(k).upper() in wanted]
        missing = wanted - {str(k).upper() for k in selected_keys}
        if missing:
            logger.warning("Pairs not found in catalogue: %s", sorted(missing))
    else:
        selected_keys = list(recs.keys())

    reference_ms = now_ms if now_ms is not None else int(datetime.now(UTC).timestamp() * 1000)

    for key in selected_keys:
        entry = recs[key]
        if not isinstance(entry, dict) or not isinstance(entry.get("primary"), dict):
            logger.warning("[%s] skipping malformed entry", key)
            continue
        primary = entry["primary"]
        try:
            metrics_cached = await _refresh_pair(
                pair=str(key).upper(),
                primary=primary,
                periods=periods,
                initial_capital=initial_capital,
                now_ms=reference_ms,
            )
        except Exception:
            logger.exception("[%s] refresh failed", key)
            continue
        if not metrics_cached:
            logger.warning("[%s] no metrics produced; leaving entry untouched", key)
            continue
        primary["metrics_cached"] = metrics_cached
        primary["metrics_computed_at"] = _now_iso()

    if not dry_run:
        target.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        logger.info("Wrote refreshed catalogue to %s", target)
    else:
        logger.info("--dry-run: catalogue not written")

    return raw


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh metrics_cached in backend/data/recommendations.yaml.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Refresh every pair in the catalogue.")
    group.add_argument(
        "--pair",
        action="append",
        metavar="PAIR",
        help="Pair to refresh (uppercase, e.g. BTCUSDT). Repeatable.",
    )
    parser.add_argument(
        "--periods",
        default=",".join(DEFAULT_PERIODS),
        help=f"Comma-separated periods. Default: {','.join(DEFAULT_PERIODS)}.",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=DEFAULT_INITIAL_CAPITAL,
        help=f"Initial capital for the backtests. Default: {DEFAULT_INITIAL_CAPITAL:g}.",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="Path to the YAML catalogue. Defaults to backend/data/recommendations.yaml.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute metrics and log them but do not rewrite the YAML.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    periods = tuple(p.strip() for p in args.periods.split(",") if p.strip())
    pairs = None if args.all else args.pair
    asyncio.run(
        refresh(
            catalog_path=args.catalog,
            pairs=pairs,
            periods=periods,
            initial_capital=args.initial_capital,
            dry_run=args.dry_run,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
