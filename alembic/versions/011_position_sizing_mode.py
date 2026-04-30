"""Risk-based position sizing (#144).

Revision ID: 011
Revises: 010
Create Date: 2026-04-30

Adds:
- ``signal_configs.position_sizing_mode`` (TEXT, default ``'full_equity'``).
  Selects how each new entry is sized. Values: ``'full_equity'`` (legacy:
  ``invested = current_portfolio * leverage``) or ``'risk_based'`` (entry
  is sized so that, if stopped, the realised loss equals
  ``current_portfolio * max_loss_per_trade_pct``, capped by the leverage
  budget).
- ``sim_trades.sizing_clipped`` (INTEGER 0/1, default 0). Set to 1 when
  ``risk_based`` sizing was clipped by the leverage budget — the realised
  loss-if-stopped will *exceed* the configured target. Surfaces in the
  SimTrades panel so under-risked entries are visible at a glance.

The SQLite path adds the same columns inline in
``backend/database.py::init_db`` with ``PRAGMA table_info`` guards. Both
paths must produce the same final schema.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "signal_configs",
        sa.Column(
            "position_sizing_mode",
            sa.Text(),
            nullable=False,
            server_default="full_equity",
        ),
    )
    op.add_column(
        "sim_trades",
        sa.Column(
            "sizing_clipped",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("sim_trades", "sizing_clipped")
    op.drop_column("signal_configs", "position_sizing_mode")
