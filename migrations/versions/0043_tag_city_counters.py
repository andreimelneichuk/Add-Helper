"""Региональные счётчики Метрики в tag_checks.

Revision ID: 0043
Revises: 0042
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tag_checks",
        sa.Column(
            "city_counters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("tag_checks", sa.Column("default_city_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tag_checks", "default_city_id")
    op.drop_column("tag_checks", "city_counters")
