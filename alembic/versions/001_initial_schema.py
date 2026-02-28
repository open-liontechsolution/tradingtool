"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-02-28

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "klines",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("interval", sa.Text, nullable=False),
        sa.Column("open_time", sa.BigInteger, nullable=False),
        sa.Column("open", sa.Text, nullable=False),
        sa.Column("high", sa.Text, nullable=False),
        sa.Column("low", sa.Text, nullable=False),
        sa.Column("close", sa.Text, nullable=False),
        sa.Column("volume", sa.Text, nullable=False),
        sa.Column("close_time", sa.BigInteger, nullable=False),
        sa.Column("quote_asset_volume", sa.Text, nullable=False),
        sa.Column("number_of_trades", sa.Integer, nullable=False),
        sa.Column("taker_buy_base_vol", sa.Text, nullable=False),
        sa.Column("taker_buy_quote_vol", sa.Text, nullable=False),
        sa.Column("ignore_field", sa.Text, nullable=True),
        sa.Column("source", sa.Text, server_default="binance_spot"),
        sa.Column("downloaded_at", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("symbol", "interval", "open_time"),
    )
    op.create_index("idx_klines_symbol_interval", "klines", ["symbol", "interval"])
    op.create_index("idx_klines_open_time", "klines", ["open_time"])

    op.create_table(
        "download_jobs",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("interval", sa.Text, nullable=False),
        sa.Column("start_time", sa.BigInteger, nullable=False),
        sa.Column("end_time", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, server_default="pending", nullable=False),
        sa.Column("progress_pct", sa.Float, server_default="0.0"),
        sa.Column("candles_downloaded", sa.Integer, server_default="0"),
        sa.Column("candles_expected", sa.Integer, server_default="0"),
        sa.Column("gaps_found", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.Column("log", sa.Text, server_default="[]"),
    )

    op.create_table(
        "derived_metrics",
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("interval", sa.Text, nullable=False),
        sa.Column("open_time", sa.BigInteger, nullable=False),
        sa.Column("metric_name", sa.Text, nullable=False),
        sa.Column("value", sa.Float, nullable=True),
        sa.PrimaryKeyConstraint("symbol", "interval", "open_time", "metric_name"),
    )
    op.create_index("idx_derived_symbol_interval", "derived_metrics", ["symbol", "interval"])

    op.create_table(
        "signal_configs",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("interval", sa.Text, nullable=False),
        sa.Column("strategy", sa.Text, nullable=False),
        sa.Column("params", sa.Text, server_default="{}", nullable=False),
        sa.Column("stop_cross_pct", sa.Float, server_default="0.02", nullable=False),
        sa.Column("portfolio", sa.Float, server_default="10000.0", nullable=False),
        sa.Column("invested_amount", sa.Float, nullable=True),
        sa.Column("leverage", sa.Float, nullable=True),
        sa.Column("cost_bps", sa.Float, server_default="10.0", nullable=False),
        sa.Column("polling_interval_s", sa.Integer, nullable=True),
        sa.Column("active", sa.Integer, server_default="1", nullable=False),
        sa.Column("last_processed_candle", sa.BigInteger, server_default="0"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_unique_constraint(
        "idx_signal_configs_unique",
        "signal_configs",
        ["symbol", "interval", "strategy", "params"],
    )

    op.create_table(
        "signals",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("config_id", sa.Integer, sa.ForeignKey("signal_configs.id"), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("interval", sa.Text, nullable=False),
        sa.Column("strategy", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("trigger_candle_time", sa.BigInteger, nullable=False),
        sa.Column("stop_price", sa.Float, nullable=False),
        sa.Column("stop_trigger_price", sa.Float, nullable=False),
        sa.Column("status", sa.Text, server_default="pending", nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
    )
    op.create_unique_constraint(
        "idx_signals_dedup", "signals", ["config_id", "trigger_candle_time"]
    )
    op.create_index("idx_signals_config", "signals", ["config_id"])

    op.create_table(
        "sim_trades",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("signal_id", sa.Integer, sa.ForeignKey("signals.id"), nullable=False),
        sa.Column("config_id", sa.Integer, sa.ForeignKey("signal_configs.id"), nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("interval", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("entry_price", sa.Float, nullable=True),
        sa.Column("entry_time", sa.BigInteger, nullable=True),
        sa.Column("stop_base", sa.Float, nullable=False),
        sa.Column("stop_trigger", sa.Float, nullable=False),
        sa.Column("exit_price", sa.Float, nullable=True),
        sa.Column("exit_time", sa.BigInteger, nullable=True),
        sa.Column("exit_reason", sa.Text, nullable=True),
        sa.Column("status", sa.Text, server_default="pending_entry", nullable=False),
        sa.Column("portfolio", sa.Float, nullable=False),
        sa.Column("invested_amount", sa.Float, nullable=False),
        sa.Column("leverage", sa.Float, nullable=False),
        sa.Column("quantity", sa.Float, nullable=True),
        sa.Column("pnl", sa.Float, nullable=True),
        sa.Column("pnl_pct", sa.Float, nullable=True),
        sa.Column("fees", sa.Float, nullable=True),
        sa.Column("equity_peak", sa.Float, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_index("idx_sim_trades_status", "sim_trades", ["status"])
    op.create_index("idx_sim_trades_config", "sim_trades", ["config_id"])

    op.create_table(
        "real_trades",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("sim_trade_id", sa.Integer, sa.ForeignKey("sim_trades.id"), nullable=True),
        sa.Column("signal_id", sa.Integer, sa.ForeignKey("signals.id"), nullable=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("entry_price", sa.Float, nullable=False),
        sa.Column("entry_time", sa.Text, nullable=False),
        sa.Column("exit_price", sa.Float, nullable=True),
        sa.Column("exit_time", sa.Text, nullable=True),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("fees", sa.Float, server_default="0.0"),
        sa.Column("pnl", sa.Float, nullable=True),
        sa.Column("pnl_pct", sa.Float, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("status", sa.Text, server_default="open", nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_index("idx_real_trades_sim", "real_trades", ["sim_trade_id"])

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("reference_type", sa.Text, nullable=False),
        sa.Column("reference_id", sa.Integer, nullable=False),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("sent_at", sa.Text, nullable=False),
    )
    op.create_unique_constraint(
        "idx_notification_dedup",
        "notification_log",
        ["event_type", "reference_type", "reference_id"],
    )


def downgrade() -> None:
    op.drop_table("notification_log")
    op.drop_table("real_trades")
    op.drop_table("sim_trades")
    op.drop_table("signals")
    op.drop_table("signal_configs")
    op.drop_table("derived_metrics")
    op.drop_table("download_jobs")
    op.drop_table("klines")
