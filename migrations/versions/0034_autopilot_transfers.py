"""Долговременная запись переносов лимита автопилота.

Revision ID: 0034
Revises: 0033
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autopilot_transfers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("donor_id", sa.Text(), nullable=False),
        sa.Column("recipient_id", sa.Text(), nullable=False),
        sa.Column("donor_start", sa.BigInteger(), nullable=False),
        sa.Column("donor_target", sa.BigInteger(), nullable=False),
        sa.Column("recipient_target", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("decision", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('open', 'done', 'rejected', 'rolled_back', 'stuck', 'cancelled')",
            name="ck_autopilot_transfers_status",
        ),
    )
    op.create_index(
        "ix_autopilot_transfers_business_status",
        "autopilot_transfers",
        ["business_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_autopilot_transfers_business_status", table_name="autopilot_transfers")
    op.drop_table("autopilot_transfers")
