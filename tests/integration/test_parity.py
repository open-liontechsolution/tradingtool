"""Parity harness: replays slot fixtures through both engines and compares trade logs.

For a fixed dataset of klines and a fixed strategy config, the backtest engine
(``backend.backtest_engine.run_backtest``) and the live engine (``signal_engine.scan_config``
+ ``live_tracker._fill_pending_entries`` + ``live_tracker._check_candle_close_exits``)
must produce structurally equivalent trades: same entries and exits, same prices,
same reasons.

The harness drives the live engine candle-by-candle with a mocked clock, so its
behaviour is deterministic. Intrabar polling (``_check_intrabar_stops``) is
intentionally skipped here:
  * Intrabar exits trigger at ``stop_base`` (post-#49) but the harness drives
    candle-close logic only — same engine semantics, same prices, but no need
    to inject worst-case ticker prices per candle. Slot expansion in Phase 5
    will optionally drive intrabar too.
  * The candle-close path (`_check_candle_close_exits`) mirrors backtest's gap
    handling and produces identical exit prices for the cases this harness
    exercises.

Trade-log comparison is structural (times, prices, side, normalized exit reason).
PnL/quantity comparison is deferred to Phase 5: backtest compounds the equity
across trades while live sizes each entry against the static ``signal_configs.portfolio``,
so absolute amounts only match for trade #1. Issue #48 (dynamic equity) closes
that gap.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.backtest_engine import run_backtest
from backend.database import get_db, init_db
from backend.download_engine import INTERVAL_MS
from backend.live_tracker import _check_candle_close_exits, _fill_pending_entries, _fill_pending_exits
from backend.signal_engine import scan_config

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "parity"


# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------


def _load_slot(name: str) -> dict:
    path = FIXTURE_DIR / f"{name}.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# DB plumbing
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    db_path = str(tmp_path / "test_parity.db")
    os.environ["DB_PATH"] = db_path
    import backend.database as dbmod  # noqa: PLC0415

    dbmod.DB_PATH = Path(db_path)
    yield


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _bulk_insert_klines(symbol: str, interval: str, candles: list[dict]) -> None:
    """Insert all slot candles into the klines table (Binance TEXT format preserved)."""
    now = _now_iso()
    rows = [
        (
            symbol,
            interval,
            int(c["open_time"]),
            str(c["open"]),
            str(c["high"]),
            str(c["low"]),
            str(c["close"]),
            str(c["volume"]),
            int(c["close_time"]),
            "0",
            0,
            "0",
            "0",
            now,
        )
        for c in candles
    ]
    async with get_db() as db:
        await db.executemany(
            """INSERT OR REPLACE INTO klines
                (symbol, interval, open_time, open, high, low, close, volume,
                 close_time, quote_asset_volume, number_of_trades,
                 taker_buy_base_vol, taker_buy_quote_vol, downloaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        await db.commit()


async def _insert_config(
    *,
    symbol: str,
    interval: str,
    strategy: str,
    params: dict,
    portfolio: float,
    cost_bps: float,
    leverage: float = 1.0,
    maintenance_margin_pct: float = 0.005,
) -> dict:
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO signal_configs
                (symbol, interval, strategy, params,
                 initial_portfolio, current_portfolio,
                 invested_amount, leverage, cost_bps, maintenance_margin_pct,
                 polling_interval_s, active, last_processed_candle,
                 created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, 1, 0, ?, ?)""",
            (
                symbol,
                interval,
                strategy,
                json.dumps(params, sort_keys=True),
                portfolio,
                portfolio,
                leverage,
                cost_bps,
                maintenance_margin_pct,
                now,
                now,
            ),
        )
        await db.commit()
        config_id = cursor.lastrowid
        cursor2 = await db.execute("SELECT * FROM signal_configs WHERE id = ?", (config_id,))
        row = await cursor2.fetchone()
        cols = [d[0] for d in cursor2.description]
    return dict(zip(cols, row, strict=False))


async def _fetch_closed_sim_trades(config_id: int) -> list[dict]:
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT side, entry_time, exit_time, entry_price, exit_price, exit_reason
               FROM sim_trades
               WHERE config_id = ? AND status = 'closed'
               ORDER BY entry_time ASC, id ASC""",
            (config_id,),
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r, strict=False)) for r in rows]


# ---------------------------------------------------------------------------
# Replay loop
# ---------------------------------------------------------------------------


def _normalize_exit_reason(raw: str) -> str:
    """Map both engines' exit reasons to a common vocabulary."""
    mapping = {
        "stop_long": "stop",
        "stop_short": "stop",
        "stop_intrabar": "stop",
        "stop_candle": "stop",
        "exit_long": "exit",
        "exit_short": "exit",
        "exit_signal": "exit",
        "liquidated": "liquidated",
        "manual": "manual",
        "config_deleted": "config_deleted",
    }
    return mapping.get(raw, raw)


async def _run_live_replay(
    config: dict,
    candles: list[dict],
    interval: str,
    warmup: int = 0,
) -> list[dict]:
    """Replay the live engine candle-by-candle. Returns closed sim_trades."""
    step_ms = INTERVAL_MS[interval]

    # ensure_candles always True (data is pre-loaded), bypass background sync.
    ensure_mock = AsyncMock(return_value=True)

    async def _scan_one_candle(closed_idx: int) -> None:
        """Drive one cycle treating candles[closed_idx] as the most recently closed."""
        closed_open = int(candles[closed_idx]["open_time"])
        # _now_ms inside the candle that follows the just-closed one. signal_engine and
        # _check_candle_close_exits both compute last_closed = current_open - step_ms,
        # so any time strictly inside (closed_open + step_ms, closed_open + 2*step_ms)
        # works. Using midpoint for safety.
        fake_now = closed_open + step_ms + step_ms // 2

        with (
            patch("backend.signal_engine._now_ms", return_value=fake_now),
            patch("backend.live_tracker._now_ms", return_value=fake_now),
            patch("backend.signal_engine.ensure_candles", ensure_mock),
            patch("backend.live_tracker.ensure_candles", ensure_mock),
        ):
            # Order matters — mirrors per-candle ordering in real-time live:
            # 1. Fill pending entries (entry signals deferred from the prior
            #    cycle fill at this candle's open).
            await _fill_pending_entries()

            # 2. Evaluate exits on the just-closed candle. May queue
            #    pending_exit (open_next mode) or close immediately
            #    (close_current).
            await _check_candle_close_exits(interval)

            # 3. Scan for new entries. Runs BEFORE _fill_pending_exits so
            #    that a trade just queued as pending_exit blocks scan in the
            #    same iteration via `_has_active_trade` — matching backtest's
            #    `exit_executed` short-circuit in open_next mode.
            await scan_config({**config})

            # 4. Fill pending exits (open_next): close at this candle's open.
            #    No-op in close_current.
            await _fill_pending_exits()

    # Iterate up to the second-to-last candle. The very last candle has no
    # successor to fill into, so we stop one short. ``warmup`` is honoured for
    # callers that want to skip noisy early candles, but defaults to 0 to mirror
    # backtest, which evaluates from t=0 (rolling windows return NaN until
    # N_entrada candles, so no signals fire until then anyway).
    for idx in range(warmup, len(candles) - 1):
        await _scan_one_candle(idx)

    return await _fetch_closed_sim_trades(config["id"])


# ---------------------------------------------------------------------------
# Comparator
# ---------------------------------------------------------------------------


def _to_comparable_bt(trade: dict) -> dict:
    return {
        "side": trade["side"],
        "entry_time": int(trade["entry_time"]),
        "exit_time": int(trade["exit_time"]),
        "entry_price": float(trade["entry_price"]),
        "exit_price": float(trade["exit_price"]),
        "exit_reason": _normalize_exit_reason(trade["exit_reason"]),
    }


def _to_comparable_live(trade: dict) -> dict:
    return {
        "side": trade["side"],
        "entry_time": int(trade["entry_time"]),
        "exit_time": int(trade["exit_time"]),
        "entry_price": float(trade["entry_price"]),
        "exit_price": float(trade["exit_price"]),
        "exit_reason": _normalize_exit_reason(trade["exit_reason"]),
    }


def assert_trade_logs_equal(bt_log: list[dict], live_log: list[dict], price_tol: float = 1e-6) -> None:
    bt_norm = [_to_comparable_bt(t) for t in bt_log]
    live_norm = [_to_comparable_live(t) for t in live_log]

    diffs: list[str] = []
    if len(bt_norm) != len(live_norm):
        diffs.append(f"trade count: bt={len(bt_norm)} live={len(live_norm)}")
        # Print up to first 5 trades from each for context
        for i in range(max(len(bt_norm), len(live_norm))):
            bt_t = bt_norm[i] if i < len(bt_norm) else None
            live_t = live_norm[i] if i < len(live_norm) else None
            diffs.append(f"  #{i}: bt={bt_t}  live={live_t}")
        raise AssertionError("\n".join(["Trade-log divergence:", *diffs]))

    for i, (bt_t, live_t) in enumerate(zip(bt_norm, live_norm, strict=True)):
        for field in ("side", "entry_time", "exit_time", "exit_reason"):
            if bt_t[field] != live_t[field]:
                diffs.append(f"#{i}.{field}: bt={bt_t[field]!r}  live={live_t[field]!r}")
        for field in ("entry_price", "exit_price"):
            if abs(bt_t[field] - live_t[field]) > price_tol:
                diffs.append(
                    f"#{i}.{field}: bt={bt_t[field]:.10f}  live={live_t[field]:.10f}  Δ={live_t[field] - bt_t[field]:+.10f}"
                )

    if diffs:
        raise AssertionError("\n".join(["Trade-log divergence:", *diffs]))


# ---------------------------------------------------------------------------
# Parametrised harness: slot × strategy
# ---------------------------------------------------------------------------
#
# Each (slot, strategy) pair is a separate test case so a divergence on one
# scenario doesn't mask the others. Tests are gated behind ``slow`` because
# each replays a full slot end-to-end (~30s on slot A, more on denser slots).
# Run with ``pytest -m slow`` (default ``pytest -q`` excludes them; CI has a
# dedicated non-blocking job).


# Strategy → params map. ``modo_ejecucion=close_current`` is the only mode
# with full engine parity post-#49 (open_next has a residual exit-fill gap
# that's its own follow-up). Strategy params are kept conservative so the
# scenarios actually generate trades on each slot's price action.
_STRATEGY_PARAMS = {
    "breakout": {
        "N_entrada": 20,
        "M_salida": 10,
        "stop_pct": 0.05,
        "modo_ejecucion": "close_current",
        "habilitar_long": True,
        "habilitar_short": True,
        "salida_por_ruptura": True,
        "coste_total_bps": 0.0,
    },
    "breakout_trailing": {
        "N_entrada": 20,
        "M_salida": 10,
        "stop_pct": 0.05,
        "trailing_lookback": 10,
        "modo_ejecucion": "close_current",
        "habilitar_long": True,
        "habilitar_short": True,
        "salida_por_ruptura": True,
        "coste_total_bps": 0.0,
    },
    "support_resistance": {
        "reversal_pct": 0.03,
        "stop_pct": 0.05,
        "modo_ejecucion": "close_current",
        "habilitar_long": True,
        "habilitar_short": True,
        "coste_total_bps": 0.0,
    },
    "support_resistance_trailing": {
        "reversal_pct": 0.03,
        "stop_pct": 0.05,
        "modo_ejecucion": "close_current",
        "habilitar_long": True,
        "habilitar_short": True,
        "coste_total_bps": 0.0,
    },
}


_SLOTS = ["slot_a", "slot_b", "slot_c", "slot_d"]
_STRATEGIES = list(_STRATEGY_PARAMS.keys())


def _enabled_slots() -> set[str]:
    """Parse the ``PARITY_SLOTS`` env var (comma-separated). Empty/unset → all slots.

    CI PR-time sets ``PARITY_SLOTS=slot_a`` to keep the harness under a minute;
    the nightly cron workflow leaves it unset so the full matrix runs.
    """
    raw = os.environ.get("PARITY_SLOTS", "").strip()
    if not raw:
        return set(_SLOTS)
    return {s.strip() for s in raw.split(",") if s.strip()}


_ENABLED_SLOTS = _enabled_slots()


async def _run_parity_case(
    slot_name: str,
    strategy_name: str,
    *,
    leverage: float = 1.0,
    maintenance_margin_pct: float = 0.005,
    expect_liquidation: bool = False,
    execution_mode: str = "close_current",
) -> None:
    slot = _load_slot(slot_name)
    symbol = slot["symbol"]
    interval = slot["interval"]
    candles = slot["candles"]

    await init_db()
    await _bulk_insert_klines(symbol, interval, candles)

    strategy_params = dict(_STRATEGY_PARAMS[strategy_name])
    strategy_params["modo_ejecucion"] = execution_mode
    if leverage > 1.0:
        # backtest_engine reads leverage + maintenance_margin_pct from params;
        # live reads them from signal_configs columns. Mirror both so the two
        # engines share the same config.
        strategy_params["leverage"] = leverage
        strategy_params["maintenance_margin_pct"] = maintenance_margin_pct
    portfolio = 10_000.0
    cost_bps = 0.0

    config = await _insert_config(
        symbol=symbol,
        interval=interval,
        strategy=strategy_name,
        params=strategy_params,
        portfolio=portfolio,
        cost_bps=cost_bps,
        leverage=leverage,
        maintenance_margin_pct=maintenance_margin_pct,
    )

    start_ms = int(candles[0]["open_time"])
    end_ms = int(candles[-1]["open_time"]) + INTERVAL_MS[interval]
    bt_result = await run_backtest(
        symbol=symbol,
        interval=interval,
        start_ms=start_ms,
        end_ms=end_ms,
        strategy_name=strategy_name,
        params=strategy_params,
        initial_capital=portfolio,
    )
    assert bt_result.error is None, f"backtest error: {bt_result.error}"
    if not expect_liquidation:
        assert not bt_result.liquidated, f"backtest liquidated unexpectedly on {slot_name}/{strategy_name}"

    live_log = await _run_live_replay(config, candles, interval, warmup=0)

    assert_trade_logs_equal(bt_result.trade_log, live_log)


@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("slot_name", _SLOTS)
@pytest.mark.parametrize("strategy_name", _STRATEGIES)
async def test_parity_slot_strategy(slot_name: str, strategy_name: str) -> None:
    """Replays slot × strategy through both engines and asserts trade-log parity (unleveraged)."""
    if slot_name not in _ENABLED_SLOTS:
        pytest.skip(f"slot {slot_name} disabled by PARITY_SLOTS env var")
    fixture_path = FIXTURE_DIR / f"{slot_name}.json.gz"
    if not fixture_path.exists():
        pytest.skip(f"fixture {fixture_path.name} not present — regenerate with the matching seeder script")
    await _run_parity_case(slot_name, strategy_name)


@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("slot_name", ["slot_a", "slot_b", "slot_c"])
@pytest.mark.parametrize("strategy_name", _STRATEGIES)
async def test_parity_open_next_slot_strategy(slot_name: str, strategy_name: str) -> None:
    """Slot × strategy × modo_ejecucion=open_next — exercises deferred fills (#58 Gap 2).

    Both backtest and live now defer entries AND exits to the next candle's
    open. Backtest queues ``pending_entry`` / ``pending_exit`` and fills on
    the next iteration; live persists ``status='pending_exit'`` with
    ``pending_exit_reason`` and closes via ``_fill_pending_exits`` at the
    next candle's open. Trade logs match bit-exact.

    Slot D (leveraged) is excluded — leverage parity already covered by
    ``test_parity_leveraged_slot_d`` in close_current; combining open_next
    + leverage is well-covered by composition.
    """
    if slot_name not in _ENABLED_SLOTS:
        pytest.skip(f"slot {slot_name} disabled by PARITY_SLOTS env var")
    fixture_path = FIXTURE_DIR / f"{slot_name}.json.gz"
    if not fixture_path.exists():
        pytest.skip(f"fixture {fixture_path.name} not present — regenerate with the matching seeder script")
    await _run_parity_case(slot_name, strategy_name, execution_mode="open_next")


@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_name", _STRATEGIES)
async def test_parity_leveraged_slot_d(strategy_name: str) -> None:
    """Slot D × strategy × leverage=10 — exercises the liquidation parity (#58 Gap 1).

    SOLUSDT 15m 2024 Q2 has wide intrabar swings; with leverage=10 most strats
    eventually trip a per-trade liquidation. Both engines are expected to:

    1. Produce the same trade list up to and including the first liquidated
       trade (exit_reason='liquidated', exit_price=liquidation_price).
    2. Stop opening new trades from that point onward (live: status='blown';
       backtest: local ``blown`` flag).
    """
    if "slot_d" not in _ENABLED_SLOTS:
        pytest.skip("slot_d disabled by PARITY_SLOTS env var")
    fixture_path = FIXTURE_DIR / "slot_d.json.gz"
    if not fixture_path.exists():
        pytest.skip("slot_d.json.gz not present — run python -m tests.fixtures.parity._seed_slot_d")
    await _run_parity_case(
        "slot_d",
        strategy_name,
        leverage=10.0,
        maintenance_margin_pct=0.005,
        expect_liquidation=True,
    )
