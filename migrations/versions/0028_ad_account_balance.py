"""Добавление last_balance_micros и balance_checked_at в ad_accounts.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ad_accounts",
        sa.Column("last_balance_micros", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "ad_accounts",
        sa.Column("balance_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ad_accounts", "balance_checked_at")
    op.drop_column("ad_accounts", "last_balance_micros")
