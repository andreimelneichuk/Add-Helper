"""Запуск рекламы из мастера: одна строка на бизнес, период бюджета создан вместе с ней.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "launches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        # Период, который начал запуск: его лимит — потолок расхода для созданных кампаний.
        sa.Column("period_id", sa.BigInteger(), nullable=False),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], name="fk_launches_business"),
        sa.ForeignKeyConstraint(["period_id"], ["budget_periods.id"], name="fk_launches_period"),
        # Второй запуск того же бизнеса создал бы вторые кампании: повтор продолжает первый.
        sa.UniqueConstraint("business_id", name="uq_launches_business_id"),
    )


def downgrade() -> None:
    op.drop_table("launches")
