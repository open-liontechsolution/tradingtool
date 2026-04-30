"""Per-config max-loss-per-trade risk filter (#142).

Revision ID: 010
Revises: 009
Create Date: 2026-04-30

Adds two columns to ``signal_configs`` driving the new entry-time risk gate:
``max_loss_per_trade_enabled`` toggles the filter, ``max_loss_per_trade_pct``
is the equity-loss threshold (e.g. 0.02 = "skip any entry whose loss-if-stopped
would exceed 2% of current_portfolio under the configured leverage").

The SQLite path adds the same columns inline in
``backend/database.py::init_db`` with ``PRAGMA table_info`` guards. Both paths
must produce the same final schema — see CLAUDE.md.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "signal_configs",
        sa.Column(
            "max_loss_per_trade_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "signal_configs",
        sa.Column(
            "max_loss_per_trade_pct",
            sa.Float(),
            nullable=False,
            server_default="0.02",
        ),
    )


def downgrade() -> None:
    op.drop_column("signal_configs", "max_loss_per_trade_pct")
    op.drop_column("signal_configs", "max_loss_per_trade_enabled")
