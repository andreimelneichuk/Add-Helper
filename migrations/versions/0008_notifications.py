"""Отправки тревог по почте и в Telegram, привязка чатов Telegram.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None

WAITING = sa.text("status IN ('pending', 'sending')")


def upgrade() -> None:
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("alert_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "uq_notification_deliveries_alert_channel",
        "notification_deliveries",
        ["alert_id", "channel"],
        unique=True,
    )
    op.create_index(
        "ix_notification_deliveries_waiting",
        "notification_deliveries",
        ["next_attempt_at"],
        postgresql_where=WAITING,
    )
    op.create_foreign_key(
        "fk_notification_deliveries_business",
        "notification_deliveries",
        "businesses",
        ["business_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_notification_deliveries_alert",
        "notification_deliveries",
        "alerts",
        ["alert_id"],
        ["id"],
    )

    op.create_table(
        "telegram_links",
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger()),
        sa.Column("code_hash", sa.Text()),
        sa.Column("code_expires_at", sa.DateTime(timezone=True)),
        sa.Column("linked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("uq_telegram_links_code_hash", "telegram_links", ["code_hash"], unique=True)
    op.create_foreign_key("fk_telegram_links_user", "telegram_links", "users", ["user_id"], ["id"])

    # Одна строка: с какого обновления бот читает сообщения дальше.
    op.create_table(
        "telegram_bot_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("update_offset", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_telegram_bot_state_single"),
    )


def downgrade() -> None:
    op.drop_table("telegram_bot_state")
    op.drop_table("telegram_links")
    op.drop_table("notification_deliveries")
