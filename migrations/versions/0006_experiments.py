"""A/B-тесты.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("campaign_id", sa.Text(), nullable=False),
        sa.Column("control_ref", sa.Text(), nullable=False),
        sa.Column("variant_ref", sa.Text(), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("expected", sa.Text(), server_default=sa.text("'A'"), nullable=False),
        sa.Column("switch_to", sa.Text(), nullable=True),
        sa.Column("switch_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("winner", sa.Text(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "uq_experiments_open_business_campaign",
        "experiments",
        ["business_id", "campaign_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('review', 'running', 'paused')"),
    )
    op.create_foreign_key(
        "fk_experiments_business", "experiments", "businesses", ["business_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_table("experiments")
