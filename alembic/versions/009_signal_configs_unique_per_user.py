"""Per-user uniqueness on signal_configs.

Revision ID: 009
Revises: 008
Create Date: 2026-04-26

The original unique index ``idx_signal_configs_unique`` was on
``(symbol, interval, strategy, params)``, which prevented two users
from independently running the same strategy on the same instrument —
Alice's config blocked Bob and leaked the existence of hers via a 409
on his create. The replacement ``idx_signal_configs_user_unique`` adds
``user_id`` as the first column so each user has their own namespace.

See #63 Sprint 2 cross-user authz tests for the regression that
exposed this.

Note on the DROP CONSTRAINT in upgrade(): the legacy index was created
with ``unique=True``, which on Postgres also auto-creates a CONSTRAINT
of the same name. ``DROP INDEX`` then fails with
``DependentObjectsStillExistError`` because the constraint depends on
the index. ``DROP CONSTRAINT`` removes both atomically. SQLite never
hits this code path — its inline migration in ``backend/database.py``
``init_db()`` uses ``DROP INDEX IF EXISTS``, which is correct because
SQLite has no constraint object separate from the index.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE signal_configs DROP CONSTRAINT IF EXISTS idx_signal_configs_unique"
    )
    op.create_index(
        "idx_signal_configs_user_unique",
        "signal_configs",
        ["user_id", "symbol", "interval", "strategy", "params"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_signal_configs_user_unique", table_name="signal_configs", if_exists=True
    )
    op.create_index(
        "idx_signal_configs_unique",
        "signal_configs",
        ["symbol", "interval", "strategy", "params"],
        unique=True,
    )
