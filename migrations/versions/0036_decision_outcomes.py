"""Исходы действий автопилота: что стало после действия и был ли откат.

Журнал решений только дополняется, поэтому исход живёт в отдельной таблице.

Revision ID: 0036
Revises: 0035
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_outcomes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("change_id", sa.BigInteger(), nullable=False),
        sa.Column("decision_id", sa.Text(), nullable=True),
        sa.Column("object_ref", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluate_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("cpl_change_bp", sa.BigInteger(), nullable=True),
        sa.Column("noise_bp", sa.BigInteger(), nullable=True),
        sa.Column("measured", JSONB(), nullable=True),
        sa.Column("rollback_change_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("rules_version", sa.Text(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "change_id", name="uq_decision_outcomes_change"),
        sa.CheckConstraint(
            "status IN ('pending', 'evaluated', 'skipped')", name="ck_decision_outcomes_status"
        ),
    )
    op.create_index(
        "ix_decision_outcomes_business_status",
        "decision_outcomes",
        ["business_id", "account", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_decision_outcomes_business_status", table_name="decision_outcomes")
    op.drop_table("decision_outcomes")
