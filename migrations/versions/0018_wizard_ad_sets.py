"""Объявления мастера до входа: набор без бизнеса, факты черновика, выбор владельца.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Адрес человека не храним: только HMAC сети /24 или /64, и только для лимита гостя.
    op.add_column("creative_sets", sa.Column("guest_key", sa.Text(), nullable=True))
    # Ключ доступа знает только браузер гостя; здесь его SHA-256. После входа остаётся,
    # чтобы повтор переноса узнал свой набор.
    op.add_column("creative_sets", sa.Column("token_hash", sa.Text(), nullable=True))
    # Факты черновика, по которым писали: у гостя профиля в базе нет.
    op.add_column(
        "creative_sets",
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Номера черновиков, которые владелец оставил в мастере.
    op.add_column(
        "creative_sets",
        sa.Column("kept", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.alter_column("creative_sets", "business_id", nullable=True)
    op.create_check_constraint(
        "ck_creative_sets_owner",
        "creative_sets",
        "business_id IS NOT NULL"
        " OR (guest_key IS NOT NULL AND token_hash IS NOT NULL AND profile IS NOT NULL)",
    )
    op.create_index(
        "ix_creative_sets_guest",
        "creative_sets",
        ["guest_key", "created_at"],
        postgresql_where=sa.text("business_id IS NULL"),
    )
    op.create_index(
        "ix_creative_sets_token",
        "creative_sets",
        ["token_hash"],
        unique=True,
        postgresql_where=sa.text("token_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM creative_sets WHERE business_id IS NULL")
    op.drop_index("ix_creative_sets_token", "creative_sets")
    op.drop_index("ix_creative_sets_guest", "creative_sets")
    op.drop_constraint("ck_creative_sets_owner", "creative_sets")
    op.alter_column("creative_sets", "business_id", nullable=False)
    op.drop_column("creative_sets", "kept")
    op.drop_column("creative_sets", "profile")
    op.drop_column("creative_sets", "token_hash")
    op.drop_column("creative_sets", "guest_key")
