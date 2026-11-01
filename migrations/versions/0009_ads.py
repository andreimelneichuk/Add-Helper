"""Витрина объявлений и их статистики по дням.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Заполняет адаптер площадки из Ads и отчёта AD_PERFORMANCE_REPORT (T6).
    op.create_table(
        "ads",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("ad_id", sa.Text(), nullable=False),
        sa.Column("campaign_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("href", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("business_id", "account", "ad_id"),
    )
    op.create_index("ix_ads_campaign", "ads", ["business_id", "account", "campaign_id"])
    op.create_foreign_key("fk_ads_business", "ads", "businesses", ["business_id"], ["id"])
    op.create_table(
        "ad_stats",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("ad_id", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("impressions", sa.BigInteger(), nullable=False),
        sa.Column("clicks", sa.BigInteger(), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("conversions", sa.BigInteger(), nullable=False),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("business_id", "account", "ad_id", "day"),
    )
    op.create_foreign_key("fk_ad_stats_business", "ad_stats", "businesses", ["business_id"], ["id"])


def downgrade() -> None:
    op.drop_table("ad_stats")
    op.drop_table("ads")
