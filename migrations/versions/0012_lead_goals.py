"""Желаемая цена обращения, которую задаёт владелец, цель.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Деньги в микроединицах, как везде; одна цель на бизнес.
    op.create_table(
        "lead_goals",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("goal_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("business_id"),
        sa.CheckConstraint("goal_micros > 0", name="ck_lead_goals_positive"),
    )


def downgrade() -> None:
    op.drop_table("lead_goals")
