"""Drop stop_cross_pct buffer: unify live with backtest on stop_base only.

Revision ID: 005
Revises: 004
Create Date: 2026-04-25

Removes:
  - signal_configs.stop_cross_pct
  - signals.stop_trigger_price
  - sim_trades.stop_trigger
  - sim_trade_stop_moves.prev_stop_trigger / new_stop_trigger

Live now closes stops at ``stop_base`` (the strategy's raw stop) — identical
to backtest. See issue #49.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("signal_configs", "stop_cross_pct")
    op.drop_column("signals", "stop_trigger_price")
    op.drop_column("sim_trades", "stop_trigger")
    op.drop_column("sim_trade_stop_moves", "prev_stop_trigger")
    op.drop_column("sim_trade_stop_moves", "new_stop_trigger")


def downgrade() -> None:
    import sqlalchemy as sa  # noqa: PLC0415

    op.add_column(
        "sim_trade_stop_moves",
        sa.Column("new_stop_trigger", sa.Float, nullable=False, server_default="0"),
    )
    op.add_column(
        "sim_trade_stop_moves",
        sa.Column("prev_stop_trigger", sa.Float, nullable=False, server_default="0"),
    )
    op.add_column(
        "sim_trades",
        sa.Column("stop_trigger", sa.Float, nullable=False, server_default="0"),
    )
    op.add_column(
        "signals",
        sa.Column("stop_trigger_price", sa.Float, nullable=False, server_default="0"),
    )
    op.add_column(
        "signal_configs",
        sa.Column("stop_cross_pct", sa.Float, nullable=False, server_default="0.02"),
    )
