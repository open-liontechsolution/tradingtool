"""Tests for the offline refresh-cache script.

The script's only side effect is rewriting the YAML, so the tests:

- mock ``run_backtest`` with a fixed BacktestResult (avoids hitting the strategy
  + DB), and
- mock ``ensure_candles`` to be a no-op (avoids touching Binance / network).

These hit the same module attribute names referenced by the script (``run_backtest``
imported into ``backend.scripts.refresh_recommendations_cache``), per the project
testing convention of patching at the consumer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from backend.backtest_engine import BacktestResult
from backend.scripts import refresh_recommendations_cache as script

# 2026-05-03 00:00:00 UTC — same calendar day as today's session so the seeded
# catalogue's validation_window remains plausible if anyone reads the result.
_REFERENCE_MS = int(datetime(2026, 5, 3, tzinfo=UTC).timestamp() * 1000)

_SEED_YAML = """\
version: 1
recommendations:
  BTCUSDT:
    primary:
      strategy: mean_reversion_bb
      timeframe: '4h'
      source: curated
      params:
        bb_period: 30
        bb_std: 3.0
      metrics_cached:
        '1y': { profit: 0.0, dd: 0.0, composite: 0.0, n_trades: 0 }
      metrics_computed_at: '2000-01-01T00:00:00Z'
"""


def _make_result(net_profit_pct: float, max_drawdown_pct: float, n_trades: int) -> BacktestResult:
    return BacktestResult(
        equity_curve=[10_000.0, 11_000.0],
        timestamps=[1, 2],
        trade_log=[],
        summary={
            "net_profit_pct": net_profit_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "n_trades": n_trades,
        },
        liquidated=False,
        error=None,
    )


# ---------------------------------------------------------------------------
# Composite formula
# ---------------------------------------------------------------------------


def test_composite_clamps_negative_profit_to_zero():
    assert script._composite(-0.10, -0.05) == 0.0


def test_composite_floor_avoids_div_by_zero():
    # dd=0 should not blow up; floor of 0.01 means composite = profit / 0.01
    assert script._composite(0.01, 0.0) == 1.0


def test_composite_typical_case():
    # +71% profit, -16% DD → 0.71 / 0.16 = 4.4375
    assert script._composite(0.71, -0.16) == pytest.approx(4.4375, rel=1e-3)


# ---------------------------------------------------------------------------
# refresh() end-to-end with mocked backtest + ensure_candles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_rewrites_metrics_cached_for_all_periods(tmp_path: Path):
    catalog = tmp_path / "rec.yaml"
    catalog.write_text(_SEED_YAML, encoding="utf-8")

    # Backtest mock: every call returns +50% profit, -10% DD, 30 trades. With
    # the conversion applied by the script (percent → fraction) these become
    # profit=0.5, dd=-0.1, composite=5.0.
    fake_run = AsyncMock(return_value=_make_result(50.0, -10.0, 30))
    fake_ensure = AsyncMock(return_value=True)

    with (
        patch.object(script, "run_backtest", new=fake_run),
        patch.object(script, "ensure_candles", new=fake_ensure),
    ):
        result = await script.refresh(
            catalog_path=catalog,
            pairs=None,
            periods=("1y", "2y", "3y", "5y"),
            now_ms=_REFERENCE_MS,
        )

    # YAML on disk reflects the refresh
    on_disk = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    primary = on_disk["recommendations"]["BTCUSDT"]["primary"]

    assert set(primary["metrics_cached"]) == {"1y", "2y", "3y", "5y"}
    for period in ("1y", "2y", "3y", "5y"):
        cell = primary["metrics_cached"][period]
        assert cell["profit"] == pytest.approx(0.5)
        assert cell["dd"] == pytest.approx(-0.1)
        assert cell["composite"] == pytest.approx(5.0)
        assert cell["n_trades"] == 30

    # metrics_computed_at is rewritten with a recent ISO timestamp (UTC, no offset issues)
    assert primary["metrics_computed_at"] != "2000-01-01T00:00:00Z"
    parsed = datetime.fromisoformat(primary["metrics_computed_at"])
    assert (datetime.now(UTC) - parsed).total_seconds() < 60

    # In-memory result mirrors disk
    assert result["recommendations"]["BTCUSDT"]["primary"]["metrics_cached"] == primary["metrics_cached"]

    # ensure_candles + run_backtest each called once per period
    assert fake_ensure.call_count == 4
    assert fake_run.call_count == 4


@pytest.mark.asyncio
async def test_refresh_dry_run_does_not_write(tmp_path: Path):
    catalog = tmp_path / "rec.yaml"
    catalog.write_text(_SEED_YAML, encoding="utf-8")
    original = catalog.read_text(encoding="utf-8")

    with (
        patch.object(script, "run_backtest", new=AsyncMock(return_value=_make_result(50.0, -10.0, 30))),
        patch.object(script, "ensure_candles", new=AsyncMock(return_value=True)),
    ):
        in_memory = await script.refresh(
            catalog_path=catalog,
            pairs=("BTCUSDT",),
            periods=("1y",),
            dry_run=True,
            now_ms=_REFERENCE_MS,
        )

    # Disk untouched
    assert catalog.read_text(encoding="utf-8") == original
    # …but the in-memory copy carries the refreshed metrics
    assert in_memory["recommendations"]["BTCUSDT"]["primary"]["metrics_cached"]["1y"]["n_trades"] == 30


@pytest.mark.asyncio
async def test_refresh_skips_pair_when_backtest_errors(tmp_path: Path, caplog):
    catalog = tmp_path / "rec.yaml"
    catalog.write_text(_SEED_YAML, encoding="utf-8")

    error_result = BacktestResult(error="Insufficient candle data for backtest")

    with (
        patch.object(script, "run_backtest", new=AsyncMock(return_value=error_result)),
        patch.object(script, "ensure_candles", new=AsyncMock(return_value=True)),
    ):
        await script.refresh(
            catalog_path=catalog,
            pairs=("BTCUSDT",),
            periods=("1y",),
            now_ms=_REFERENCE_MS,
        )

    on_disk = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    # Original seed cell preserved — entry not partially overwritten
    assert on_disk["recommendations"]["BTCUSDT"]["primary"]["metrics_cached"] == {
        "1y": {"profit": 0.0, "dd": 0.0, "composite": 0.0, "n_trades": 0},
    }
    assert on_disk["recommendations"]["BTCUSDT"]["primary"]["metrics_computed_at"] == "2000-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_refresh_pairs_filter_warns_about_missing(tmp_path: Path, caplog):
    catalog = tmp_path / "rec.yaml"
    catalog.write_text(_SEED_YAML, encoding="utf-8")

    with (
        patch.object(script, "run_backtest", new=AsyncMock(return_value=_make_result(10.0, -5.0, 5))),
        patch.object(script, "ensure_candles", new=AsyncMock(return_value=True)),
    ):
        with caplog.at_level("WARNING"):
            await script.refresh(
                catalog_path=catalog,
                pairs=("BTCUSDT", "DOGEUSDT"),
                periods=("1y",),
                now_ms=_REFERENCE_MS,
            )

    assert any("DOGEUSDT" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_refresh_unknown_period_raises(tmp_path: Path):
    catalog = tmp_path / "rec.yaml"
    catalog.write_text(_SEED_YAML, encoding="utf-8")

    with (
        patch.object(script, "run_backtest", new=AsyncMock(return_value=_make_result(0.0, 0.0, 0))),
        patch.object(script, "ensure_candles", new=AsyncMock(return_value=True)),
    ):
        # _refresh_pair raises ValueError, which the top-level loop catches
        # and turns into a logged exception — so the catalogue is left
        # untouched but the script doesn't propagate.
        await script.refresh(
            catalog_path=catalog,
            pairs=("BTCUSDT",),
            periods=("99y",),  # not in _PERIOD_DAYS
            now_ms=_REFERENCE_MS,
        )

    on_disk = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    # No metrics produced → entry untouched
    assert on_disk["recommendations"]["BTCUSDT"]["primary"]["metrics_computed_at"] == "2000-01-01T00:00:00Z"


def test_refresh_missing_catalog_raises(tmp_path: Path):
    import asyncio

    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(FileNotFoundError, match="not found"):
        asyncio.run(script.refresh(catalog_path=missing, pairs=("BTCUSDT",)))
