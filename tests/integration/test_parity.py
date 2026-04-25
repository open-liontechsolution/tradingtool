"""Parity harness: replays slot fixtures through both engines and compares trade logs.

For a fixed dataset of klines and a fixed strategy config, the backtest engine
(``backend.backtest_engine.run_backtest``) and the live engine (``signal_engine.scan_config``
+ ``live_tracker._fill_pending_entries`` + ``live_tracker._check_candle_close_exits``)
must produce structurally equivalent trades: same entries and exits, same prices,
same reasons.

The harness drives the live engine candle-by-candle with a mocked clock, so its
behaviour is deterministic. Intrabar polling (``_check_intrabar_stops``) is
intentionally skipped here:
  * The intrabar path executes at the live ``stop_trigger`` regardless of the actual
    ticker price, so on a gap candle (open already past stop) it diverges from
    backtest, which fills at the open price. Issue #49 removes the trigger buffer
    and unifies that codepath; once it lands, the harness will be extended to
    cover intrabar in Phase 5.
  * The candle-close path (`_check_candle_close_exits`) does mirror backtest's gap
    handling (line 626-635 of live_tracker.py) and produces identical exit prices
    for the cases this harness exercises.

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
from backend.live_tracker import _check_candle_close_exits, _fill_pending_entries
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
    stop_cross_pct: float = 0.0,
) -> dict:
    now = _now_iso()
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO signal_configs
                (symbol, interval, strategy, params, stop_cross_pct,
                 portfolio, invested_amount, leverage, cost_bps,
                 polling_interval_s, active, last_processed_candle,
                 created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, NULL, 1.0, ?, NULL, 1, 0, ?, ?)""",
            (
                symbol,
                interval,
                strategy,
                json.dumps(params, sort_keys=True),
                stop_cross_pct,
                portfolio,
                cost_bps,
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
            # 1. Fill pending entries from the prior cycle at this candle's open
            #    (open_next semantics: trigger at candles[closed_idx-1], fill at closed_idx.open).
            await _fill_pending_entries()

            # 2. Evaluate exits on the just-closed candle.
            await _check_candle_close_exits(interval)

            # 3. Scan for new entry signal on the just-closed candle.
            await scan_config({**config})

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
# Tests — slot A
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slot_a_breakout_close_current_default():
    """Slot A × breakout × close_current × stop_cross_pct=0 produces matching trade logs.

    ``close_current`` is the execution mode where the engines line up cleanly:
    both fill entry and exit at the close of the trigger candle, so exit_price
    parity is exact. ``open_next`` has a separate gap to track (backtest's exit
    fills at the *current* candle's open while live fills at close — see
    backtest_engine.py:127), which is out of scope for Phase 1.
    """
    slot = _load_slot("slot_a")
    symbol = slot["symbol"]
    interval = slot["interval"]
    candles = slot["candles"]
    assert len(candles) > 100, "slot A fixture too small"

    await init_db()
    await _bulk_insert_klines(symbol, interval, candles)

    strategy_params = {
        "N_entrada": 20,
        "M_salida": 10,
        "stop_pct": 0.05,
        "modo_ejecucion": "close_current",
        "habilitar_long": True,
        "habilitar_short": True,
        "salida_por_ruptura": True,
        "coste_total_bps": 0.0,
    }
    portfolio = 10_000.0
    cost_bps = 0.0

    config = await _insert_config(
        symbol=symbol,
        interval=interval,
        strategy="breakout",
        params=strategy_params,
        portfolio=portfolio,
        cost_bps=cost_bps,
        stop_cross_pct=0.0,
    )

    # Backtest over the full slot range
    start_ms = int(candles[0]["open_time"])
    end_ms = int(candles[-1]["open_time"]) + INTERVAL_MS[interval]
    bt_result = await run_backtest(
        symbol=symbol,
        interval=interval,
        start_ms=start_ms,
        end_ms=end_ms,
        strategy_name="breakout",
        params=strategy_params,
        initial_capital=portfolio,
    )
    assert bt_result.error is None, f"backtest error: {bt_result.error}"
    assert not bt_result.liquidated, "backtest liquidated unexpectedly"

    # Live replay over the same slot
    live_log = await _run_live_replay(config, candles, interval, warmup=0)

    assert_trade_logs_equal(bt_result.trade_log, live_log)
