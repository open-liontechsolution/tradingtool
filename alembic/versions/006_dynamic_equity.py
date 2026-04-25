"""Dynamic equity per config: rename portfolio to initial_portfolio, add current_portfolio.

Revision ID: 006
Revises: 005
Create Date: 2026-04-25

Renames ``signal_configs.portfolio`` to ``initial_portfolio`` (immutable
starting capital) and adds ``current_portfolio`` (mutable, evolves with PnL of
closed sim_trades). ``sim_trades.portfolio`` stays as the per-trade snapshot of
``current_portfolio`` at entry time.

See issue #48.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("signal_configs", "portfolio", new_column_name="initial_portfolio")
    op.add_column(
        "signal_configs",
        sa.Column("current_portfolio", sa.Float, nullable=False, server_default="0"),
    )
    # Seed current_portfolio with the existing initial_portfolio so live configs
    # behave identically until the first sim_trade closes after upgrade.
    op.execute("UPDATE signal_configs SET current_portfolio = initial_portfolio")


def downgrade() -> None:
    op.drop_column("signal_configs", "current_portfolio")
    op.alter_column("signal_configs", "initial_portfolio", new_column_name="portfolio")
