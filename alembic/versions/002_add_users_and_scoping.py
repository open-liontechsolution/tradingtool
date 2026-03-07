"""Add users table and user_id to signal_configs

Revision ID: 002
Revises: 001
Create Date: 2026-03-07

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("keycloak_sub", sa.Text, nullable=False, unique=True),
        sa.Column("email", sa.Text, nullable=True),
        sa.Column("username", sa.Text, nullable=True),
        sa.Column("roles", sa.Text, server_default="[]", nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("last_login_at", sa.Text, nullable=False),
    )

    op.add_column(
        "signal_configs",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("idx_signal_configs_user", "signal_configs", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_signal_configs_user", table_name="signal_configs")
    op.drop_column("signal_configs", "user_id")
    op.drop_table("users")
