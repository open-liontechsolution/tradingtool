"""Leverage liquidation + blown account state.

Revision ID: 007
Revises: 006
Create Date: 2026-04-25

Adds the schema needed to model leveraged liquidation in live trading
(issue #50):

  signal_configs:
    - maintenance_margin_pct  (default 0.005 — Binance-ish baseline)
    - status                  ('active' | 'paused' | 'blown')
    - blown_at                (timestamp when current_portfolio hit ≤0)

  sim_trades:
    - liquidation_price       (computed at entry fill from leverage + mm;
                                NULL when leverage<=1)

The 'liquidated' value also becomes a valid ``sim_trades.exit_reason`` — but
``exit_reason`` is a free-text TEXT column on the table, so no schema change
is needed for that.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "signal_configs",
        sa.Column(
            "maintenance_margin_pct",
            sa.Float,
            nullable=False,
            server_default="0.005",
        ),
    )
    op.add_column(
        "signal_configs",
        sa.Column(
            "status",
            sa.Text,
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "signal_configs",
        sa.Column("blown_at", sa.Text, nullable=True),
    )
    op.add_column(
        "sim_trades",
        sa.Column("liquidation_price", sa.Float, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sim_trades", "liquidation_price")
    op.drop_column("signal_configs", "blown_at")
    op.drop_column("signal_configs", "status")
    op.drop_column("signal_configs", "maintenance_margin_pct")
