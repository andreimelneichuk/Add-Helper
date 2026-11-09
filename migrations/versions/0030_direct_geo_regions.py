"""Persist the Direct geographic reference tree.

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("business_profiles", sa.Column("target_region_id", sa.BigInteger()))
    op.add_column("business_profiles", sa.Column("target_city_name", sa.Text()))
    op.add_column("business_profiles", sa.Column("target_region_display", sa.Text()))
    op.create_table(
        "direct_geo_regions",
        sa.Column("cache_id", sa.Integer(), primary_key=True),
        sa.Column("regions", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("cache_id = 1", name="ck_direct_geo_regions_singleton"),
    )


def downgrade() -> None:
    op.drop_table("direct_geo_regions")
    op.drop_column("business_profiles", "target_region_display")
    op.drop_column("business_profiles", "target_city_name")
    op.drop_column("business_profiles", "target_region_id")
