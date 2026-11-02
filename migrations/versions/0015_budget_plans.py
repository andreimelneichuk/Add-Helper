"""План бюджета до запуска: сумма из мастера.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Один план на бизнес; деньги в микроединицах. Лимиты площадки по нему ставит запуск.
    op.create_table(
        "budget_plans",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("monthly_micros", sa.BigInteger(), nullable=False),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("business_id"),
        sa.CheckConstraint("monthly_micros > 0", name="ck_budget_plans_positive"),
    )


def downgrade() -> None:
    op.drop_table("budget_plans")
