"""Объявления под места в направлениях, без одобрения владельцем

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Набор пишется для места в направлении; старые наборы без места остаются как есть.
    op.add_column("creative_sets", sa.Column("account", sa.Text()))
    op.add_column("creative_sets", sa.Column("campaign_id", sa.Text()))
    op.add_column("creative_sets", sa.Column("reason", sa.Text()))
    op.add_column(
        "creative_sets", sa.Column("wanted", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "creative_sets", sa.Column("judged", sa.Boolean(), nullable=False, server_default="false")
    )
    op.create_table(
        "creatives",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("set_id", sa.BigInteger(), nullable=True),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("campaign_id", sa.Text(), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("auto_ok", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=False),
        sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["set_id"], ["creative_sets.id"], name="fk_creatives_set_id_creative_sets"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_creatives"),
    )
    op.create_index("ix_creatives_business_id_state", "creatives", ["business_id", "state"])
    op.create_foreign_key(
        "fk_creatives_business", "creatives", "businesses", ["business_id"], ["id"]
    )
    # Кому показываем: регионы, фразы, время. Заполнит адаптер площадки (T6).
    op.add_column(
        "campaigns",
        sa.Column(
            "targeting",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "targeting")
    op.drop_table("creatives")
    for name in ("judged", "wanted", "reason", "campaign_id", "account"):
        op.drop_column("creative_sets", name)
