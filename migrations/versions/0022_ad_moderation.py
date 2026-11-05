"""Отправка объявлений в Директ и итог проверки: номер на площадке, ремонты, причина отказа.

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("creatives", sa.Column("ad_id", sa.Text(), nullable=True))
    op.add_column(
        "creatives", sa.Column("repairs", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("creatives", sa.Column("rejection", sa.Text(), nullable=True))
    op.create_index(
        "ix_creatives_ad_id",
        "creatives",
        ["business_id", "account", "ad_id"],
        unique=True,
        postgresql_where=sa.text("ad_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_creatives_ad_id", table_name="creatives")
    op.drop_column("creatives", "rejection")
    op.drop_column("creatives", "repairs")
    op.drop_column("creatives", "ad_id")
