"""Tests for backtest_engine: trade signals, stop loss, bankruptcy detection."""

from __future__ import annotations

import pandas as pd
import pytest

import backend.backtest_engine as _be_module
from backend.backtest_engine import _compute_pnl, _compute_pnl_no_fees, run_backtest
from backend.download_engine import INTERVAL_MS
from backend.strategies.base import PositionState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
    symbol              TEXT    NOT NULL,
    interval            TEXT    NOT NULL,
    open_time           INTEGER NOT NULL,
    open                TEXT    NOT NULL,
    high                TEXT    NOT NULL,
    low                 TEXT    NOT NULL,
    close               TEXT    NOT NULL,
    volume              TEXT    NOT NULL,
    close_time          INTEGER NOT NULL,
    quote_asset_volume  TEXT    NOT NULL,
    number_of_trades    INTEGER NOT NULL,
    taker_buy_base_vol  TEXT    NOT NULL,
    taker_buy_quote_vol TEXT    NOT NULL,
    ignore_field        TEXT,
    source              TEXT    DEFAULT 'binance_spot',
    downloaded_at       TEXT    NOT NULL,
    PRIMARY KEY (symbol, interval, open_time)
);
"""


def _make_row(open_time, open_, high, low, close, interval="1d"):
    step = INTERVAL_MS[interval]
    return {
        "symbol": "BTCUSDT",
        "interval": interval,
        "open_time": open_time,
        "open": str(open_),
        "high": str(high),
        "low": str(low),
        "close": str(close),
        "volume": "1000.0",
        "close_time": open_time + step - 1,
        "quote_asset_volume": "1000000.0",
        "number_of_trades": 100,
        "taker_buy_base_vol": "500.0",
        "taker_buy_quote_vol": "500000.0",
        "ignore_field": "0",
        "source": "binance_spot",
        "downloaded_at": "2024-01-01T00:00:00+00:00",
    }


def _candles_to_df(candles: list[dict]) -> pd.DataFrame:
    """Convert a list of candle dicts to the DataFrame format load_candles_df returns."""
    if not candles:
        return pd.DataFrame()
    rows = [
        (
            c["open_time"],
            float(c["open"]),
            float(c["high"]),
            float(c["low"]),
            float(c["close"]),
            float(c["volume"]),
        )
        for c in candles
    ]
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    df["open_time"] = df["open_time"].astype(int)
    return df


def _build_trending_up_candles(n: int = 100, start_price: float = 100.0):
    """Build candles that trend up then pull back sharply, triggering entry + exit."""
    step = INTERVAL_MS["1d"]
    candles = []
    price = start_price
    # Phase 1: 60 candles trending up (triggers breakout entry)
    for i in range(60):
        open_ = price
        close = price + 2.0
        high = close + 1.0
        low = open_ - 0.5
        candles.append(_make_row(step * i, open_, high, low, close))
        price = close
    # Phase 2: 40 candles dropping sharply (triggers min_exit and closes position)
    for i in range(40):
        open_ = price
        close = price - 5.0
        high = open_ + 0.5
        low = close - 1.0
        candles.append(_make_row(step * (60 + i), open_, high, low, close))
        price = close
    return candles[:n]


def _build_flat_candles(n: int = 60, price: float = 100.0):
    """Build sideways candles that never break out."""
    step = INTERVAL_MS["1d"]
    candles = []
    for i in range(n):
        candles.append(_make_row(step * i, price, price + 0.1, price - 0.1, price))
    return candles


# ---------------------------------------------------------------------------
# _compute_pnl helpers
# ---------------------------------------------------------------------------


class TestComputePnl:
    def test_long_profit(self):
        state = PositionState(side="long", entry_price=100.0, quantity=10.0)
        pnl = _compute_pnl(state, exec_price=110.0, cost_factor=0.0, equity=1000.0)
        assert abs(pnl - 100.0) < 1e-9

    def test_long_loss(self):
        state = PositionState(side="long", entry_price=100.0, quantity=10.0)
        pnl = _compute_pnl(state, exec_price=90.0, cost_factor=0.0, equity=1000.0)
        assert abs(pnl - (-100.0)) < 1e-9

    def test_short_profit(self):
        state = PositionState(side="short", entry_price=100.0, quantity=10.0)
        pnl = _compute_pnl(state, exec_price=90.0, cost_factor=0.0, equity=1000.0)
        assert abs(pnl - 100.0) < 1e-9

    def test_fees_reduce_pnl(self):
        state = PositionState(side="long", entry_price=100.0, quantity=10.0)
        pnl_no_fee = _compute_pnl(state, exec_price=110.0, cost_factor=0.0, equity=1000.0)
        pnl_with_fee = _compute_pnl(state, exec_price=110.0, cost_factor=0.001, equity=1000.0)
        assert pnl_with_fee < pnl_no_fee

    def test_no_fees_matches_no_fee_helper(self):
        state = PositionState(side="long", entry_price=100.0, quantity=5.0)
        assert _compute_pnl(state, 120.0, 0.0, 500.0) == _compute_pnl_no_fees(state, 120.0)


# ---------------------------------------------------------------------------
# Fixture: patch load_candles_df on the backtest_engine module directly
# ---------------------------------------------------------------------------


def _patch_candles(monkeypatch, candles: list[dict]):
    """Patch backtest_engine.load_candles_df to return candles as a DataFrame."""
    df = _candles_to_df(candles)

    async def _mock_load(*args, **kwargs):
        return df

    monkeypatch.setattr(_be_module, "load_candles_df", _mock_load)


# ---------------------------------------------------------------------------
# Backtest engine integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insufficient_data_returns_error(monkeypatch):
    _patch_candles(monkeypatch, [])  # empty → error
    result = await run_backtest(
        symbol="BTCUSDT",
        interval="1d",
        start_ms=0,
        end_ms=INTERVAL_MS["1d"] * 5,
        strategy_name="breakout",
        params={},
        initial_capital=10_000.0,
    )
    assert result.error is not None


@pytest.mark.asyncio
async def test_flat_market_produces_no_trades(monkeypatch):
    _patch_candles(monkeypatch, _build_flat_candles(n=60))

    step = INTERVAL_MS["1d"]
    result = await run_backtest(
        symbol="BTCUSDT",
        interval="1d",
        start_ms=0,
        end_ms=step * 60,
        strategy_name="breakout",
        params={"N_entrada": 20, "M_salida": 10, "stop_pct": 0.05, "habilitar_long": True, "habilitar_short": True},
        initial_capital=10_000.0,
    )
    assert result.error is None
    assert len(result.trade_log) == 0
    assert len(result.equity_curve) > 0


@pytest.mark.asyncio
async def test_trending_market_produces_trades(monkeypatch):
    _patch_candles(monkeypatch, _build_trending_up_candles(n=100))

    step = INTERVAL_MS["1d"]
    result = await run_backtest(
        symbol="BTCUSDT",
        interval="1d",
        start_ms=0,
        end_ms=step * 100,
        strategy_name="breakout",
        params={"N_entrada": 20, "M_salida": 10, "stop_pct": 0.05, "habilitar_long": True, "habilitar_short": False},
        initial_capital=10_000.0,
    )
    assert result.error is None
    assert len(result.trade_log) >= 1
    assert result.equity_curve[-1] > 10_000.0  # profitable in uptrend


@pytest.mark.asyncio
async def test_equity_curve_length_matches_candles(monkeypatch):
    _patch_candles(monkeypatch, _build_flat_candles(n=50))

    step = INTERVAL_MS["1d"]
    result = await run_backtest(
        symbol="BTCUSDT",
        interval="1d",
        start_ms=0,
        end_ms=step * 50,
        strategy_name="breakout",
        params={"N_entrada": 20, "M_salida": 10, "stop_pct": 0.05},
        initial_capital=10_000.0,
    )
    assert len(result.equity_curve) == 50


@pytest.mark.asyncio
async def test_bankruptcy_detection(monkeypatch):
    """For normal runs, liquidated should be False."""
    _patch_candles(monkeypatch, _build_trending_up_candles(n=60))

    step = INTERVAL_MS["1d"]
    result = await run_backtest(
        symbol="BTCUSDT",
        interval="1d",
        start_ms=0,
        end_ms=step * 60,
        strategy_name="breakout",
        params={"N_entrada": 20, "M_salida": 10, "stop_pct": 0.05},
        initial_capital=10_000.0,
    )
    assert not result.liquidated


@pytest.mark.asyncio
async def test_trade_log_fields_present(monkeypatch):
    _patch_candles(monkeypatch, _build_trending_up_candles(n=100))

    step = INTERVAL_MS["1d"]
    result = await run_backtest(
        symbol="BTCUSDT",
        interval="1d",
        start_ms=0,
        end_ms=step * 100,
        strategy_name="breakout",
        params={"N_entrada": 20, "M_salida": 10, "stop_pct": 0.05, "habilitar_long": True, "habilitar_short": False},
        initial_capital=10_000.0,
    )
    if result.trade_log:
        required = {
            "entry_time",
            "exit_time",
            "side",
            "entry_price",
            "exit_price",
            "pnl",
            "fees",
            "exit_reason",
            "duration_candles",
        }
        for trade in result.trade_log:
            assert required.issubset(set(trade.keys()))


@pytest.mark.asyncio
async def test_summary_metrics_present(monkeypatch):
    _patch_candles(monkeypatch, _build_trending_up_candles(n=100))

    step = INTERVAL_MS["1d"]
    result = await run_backtest(
        symbol="BTCUSDT",
        interval="1d",
        start_ms=0,
        end_ms=step * 100,
        strategy_name="breakout",
        params={"N_entrada": 20, "M_salida": 10, "stop_pct": 0.05, "habilitar_long": True, "habilitar_short": False},
        initial_capital=10_000.0,
    )
    required_keys = {"net_profit", "net_profit_pct", "max_drawdown_pct", "sharpe", "n_trades", "win_rate_pct"}
    assert required_keys.issubset(set(result.summary.keys()))
