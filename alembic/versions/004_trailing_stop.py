"""Trailing stop: audit trail for stop movements

Revision ID: 004
Revises: 003
Create Date: 2026-04-24

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sim_trade_stop_moves",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "sim_trade_id",
            sa.Integer,
            sa.ForeignKey("sim_trades.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prev_stop_base", sa.Float, nullable=False),
        sa.Column("new_stop_base", sa.Float, nullable=False),
        sa.Column("prev_stop_trigger", sa.Float, nullable=False),
        sa.Column("new_stop_trigger", sa.Float, nullable=False),
        sa.Column("candle_time", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
    )
    op.create_index(
        "idx_sim_trade_stop_moves_trade",
        "sim_trade_stop_moves",
        ["sim_trade_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_sim_trade_stop_moves_trade", table_name="sim_trade_stop_moves")
    op.drop_table("sim_trade_stop_moves")
