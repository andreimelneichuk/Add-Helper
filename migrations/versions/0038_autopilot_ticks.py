"""Журнал часовых тиков автопилота: по нему считаются пропуски воркера (этап 8).

Одна строка на час (UTC). Повторный запуск того же часа обновляет строку, а не дублирует.

Revision ID: 0038
Revises: 0037
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autopilot_ticks",
        sa.Column("slot", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ran_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("accounts", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("slot"),
    )


def downgrade() -> None:
    op.drop_table("autopilot_ticks")
