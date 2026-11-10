"""Группа объявления на площадке: без неё автопилот считает каждое объявление последним.

Колонка пустая по умолчанию, старый код её не читает: миграция обратно совместима.

Revision ID: 0040
Revises: 0039
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ads", sa.Column("group_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ads", "group_id")
