"""Время итога A/B-теста: «Сегодня» показывает карточку итога неделю после него.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "experiments", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("experiments", "finished_at")
