"""Telegram notifications: user chat link, per-config toggle, channel in notification_log

Revision ID: 003
Revises: 002
Create Date: 2026-04-23

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users: Telegram link columns ---------------------------------------
    op.add_column("users", sa.Column("telegram_chat_id", sa.BigInteger, nullable=True))
    op.add_column("users", sa.Column("telegram_username", sa.Text, nullable=True))
    op.add_column("users", sa.Column("telegram_linked_at", sa.Text, nullable=True))
    # Partial unique index: one chat_id per user, many NULLs allowed.
    op.create_index(
        "idx_users_telegram_chat_id",
        "users",
        ["telegram_chat_id"],
        unique=True,
        postgresql_where=sa.text("telegram_chat_id IS NOT NULL"),
    )

    # --- signal_configs: per-alert Telegram toggle --------------------------
    op.add_column(
        "signal_configs",
        sa.Column("telegram_enabled", sa.Integer, server_default="0", nullable=False),
    )

    # --- telegram_link_tokens: opaque codes pasted via "/start <token>" -----
    op.create_table(
        "telegram_link_tokens",
        sa.Column("token", sa.Text, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("expires_at", sa.Text, nullable=False),
        sa.Column("used_at", sa.Text, nullable=True),
    )
    op.create_index(
        "idx_telegram_link_tokens_user",
        "telegram_link_tokens",
        ["user_id"],
    )

    # --- notification_log: add channel + user_id, swap unique constraint ----
    op.add_column(
        "notification_log",
        sa.Column("channel", sa.Text, server_default="internal", nullable=False),
    )
    op.add_column(
        "notification_log",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
    )
    op.drop_constraint("idx_notification_dedup", "notification_log", type_="unique")
    op.create_unique_constraint(
        "idx_notification_dedup",
        "notification_log",
        ["event_type", "reference_type", "reference_id", "channel"],
    )


def downgrade() -> None:
    # Reverse of upgrade, strict inverse order.
    op.drop_constraint("idx_notification_dedup", "notification_log", type_="unique")
    op.create_unique_constraint(
        "idx_notification_dedup",
        "notification_log",
        ["event_type", "reference_type", "reference_id"],
    )
    op.drop_column("notification_log", "user_id")
    op.drop_column("notification_log", "channel")

    op.drop_index("idx_telegram_link_tokens_user", table_name="telegram_link_tokens")
    op.drop_table("telegram_link_tokens")

    op.drop_column("signal_configs", "telegram_enabled")

    op.drop_index("idx_users_telegram_chat_id", table_name="users")
    op.drop_column("users", "telegram_linked_at")
    op.drop_column("users", "telegram_username")
    op.drop_column("users", "telegram_chat_id")
