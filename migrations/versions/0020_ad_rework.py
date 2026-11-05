"""Переделка одного объявления по просьбе: просьба в наборе, вариант знает, что заменяет.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Номер объявления площадки, его текст на момент просьбы, просьба и версия правил.
    op.add_column(
        "creative_sets",
        sa.Column("rework", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Объявление площадки, которое заменит выбранный вариант.
    op.add_column("creatives", sa.Column("replaces", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("creatives", "replaces")
    op.drop_column("creative_sets", "rework")
