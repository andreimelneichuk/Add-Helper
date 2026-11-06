"""Режим бюджета владельца: раздельный на сайт или общий на все сайты.

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Одна строка на владельца; сумма в микроединицах. Планы сайтов лежат в budget_plans.
    op.create_table(
        "budget_portfolios",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("shared_monthly_micros", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
        sa.CheckConstraint("mode IN ('separate', 'shared')", name="ck_budget_portfolios_mode"),
        sa.CheckConstraint(
            "(mode = 'shared' AND shared_monthly_micros > 0)"
            " OR (mode = 'separate' AND shared_monthly_micros IS NULL)",
            name="ck_budget_portfolios_shared_sum",
        ),
    )


def downgrade() -> None:
    op.drop_table("budget_portfolios")
