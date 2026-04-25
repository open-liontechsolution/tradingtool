"""Sim-trade pending_exit lifecycle for open_next mode.

Revision ID: 008
Revises: 007
Create Date: 2026-04-25

Adds ``sim_trades.pending_exit_reason`` (TEXT, nullable). When the strategy
emits an exit/stop signal on candle t and the config's ``modo_ejecucion`` is
``open_next``, the live engine flips the trade to ``status='pending_exit'``
and records the reason here. A separate fill function then closes the trade
at the next candle's open.

See issue #58 Gap 2.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sim_trades",
        sa.Column("pending_exit_reason", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sim_trades", "pending_exit_reason")
