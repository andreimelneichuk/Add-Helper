"""Разбор сайта до входа: строка без бизнеса, доступ по ключу, лимит по адресу.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Адрес человека не храним: только ключ HMAC от сети /24 или /64, и только для лимита.
    op.add_column("site_reads", sa.Column("guest_key", sa.Text(), nullable=True))
    # Сам ключ доступа знает только браузер гостя; здесь его SHA-256.
    op.add_column("site_reads", sa.Column("token_hash", sa.Text(), nullable=True))
    op.alter_column("site_reads", "business_id", nullable=True)
    op.create_check_constraint(
        "ck_site_reads_owner",
        "site_reads",
        "(business_id IS NOT NULL AND guest_key IS NULL AND token_hash IS NULL)"
        " OR (business_id IS NULL AND guest_key IS NOT NULL AND token_hash IS NOT NULL)",
    )
    op.create_index(
        "ix_site_reads_guest",
        "site_reads",
        ["guest_key", "created_at"],
        postgresql_where=sa.text("business_id IS NULL"),
    )
    op.create_index(
        "ix_site_reads_token",
        "site_reads",
        ["token_hash"],
        unique=True,
        postgresql_where=sa.text("token_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM site_reads WHERE business_id IS NULL")
    op.drop_index("ix_site_reads_token", "site_reads")
    op.drop_index("ix_site_reads_guest", "site_reads")
    op.drop_constraint("ck_site_reads_owner", "site_reads")
    op.alter_column("site_reads", "business_id", nullable=False)
    op.drop_column("site_reads", "token_hash")
    op.drop_column("site_reads", "guest_key")
