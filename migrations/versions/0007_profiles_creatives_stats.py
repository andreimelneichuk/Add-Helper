"""Сверка со счётом от точки отсчёта, профиль бизнеса, наборы объявлений, витрины статистики.

Revision ID: 0007
Revises: 0006
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None

EMPTY_LIST = sa.text("'[]'::jsonb")


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.add_column("budget_periods", sa.Column("opening_spent_micros", sa.BigInteger()))
    op.add_column("budget_periods", sa.Column("last_balance_micros", sa.BigInteger()))
    op.add_column("budget_periods", sa.Column("balance_checked_at", sa.DateTime(timezone=True)))

    op.create_table(
        "business_profiles",
        sa.Column("business_id", sa.Uuid(), primary_key=True),
        sa.Column("services", postgresql.JSONB(), server_default=EMPTY_LIST, nullable=False),
        sa.Column("address", sa.Text(), server_default="", nullable=False),
        sa.Column("features", postgresql.JSONB(), server_default=EMPTY_LIST, nullable=False),
        sa.Column("niche", sa.Text(), server_default="", nullable=False),
        sa.Column("website", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_foreign_key(
        "fk_business_profiles_business", "business_profiles", "businesses", ["business_id"], ["id"]
    )

    op.create_table(
        "creative_sets",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("source", sa.Text()),
        sa.Column("drafts", postgresql.JSONB(), server_default=EMPTY_LIST, nullable=False),
        sa.Column("rejected", postgresql.JSONB(), server_default=EMPTY_LIST, nullable=False),
        sa.Column("notes", postgresql.JSONB(), server_default=EMPTY_LIST, nullable=False),
        sa.Column("tokens_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.false(), nullable=False),
        _created_at(),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_creative_sets_waiting",
        "creative_sets",
        ["status", "created_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index("ix_creative_sets_business_id", "creative_sets", ["business_id", "id"])
    op.create_foreign_key(
        "fk_creative_sets_business", "creative_sets", "businesses", ["business_id"], ["id"]
    )

    # Витрины для экранов. Заполняет адаптер площадки из Campaigns и Reports (T6).
    op.create_table(
        "campaigns",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("campaign_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("limit_micros", sa.BigInteger()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("business_id", "account", "campaign_id"),
    )
    op.create_foreign_key(
        "fk_campaigns_business", "campaigns", "businesses", ["business_id"], ["id"]
    )
    op.create_table(
        "campaign_stats",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("campaign_id", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("impressions", sa.BigInteger(), nullable=False),
        sa.Column("clicks", sa.BigInteger(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("conversions", sa.BigInteger(), nullable=False),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("business_id", "account", "campaign_id", "day"),
    )
    op.create_foreign_key(
        "fk_campaign_stats_business", "campaign_stats", "businesses", ["business_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_table("campaign_stats")
    op.drop_table("campaigns")
    op.drop_table("creative_sets")
    op.drop_table("business_profiles")
    op.drop_column("budget_periods", "balance_checked_at")
    op.drop_column("budget_periods", "last_balance_micros")
    op.drop_column("budget_periods", "opening_spent_micros")
