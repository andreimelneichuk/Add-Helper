"""Журнал решений автопилота и включение объявлений в журнал изменений.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("object_ref", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decisions_business_id_account", "decisions", ["business_id", "account"])
    op.create_foreign_key(
        "fk_decisions_business", "decisions", "businesses", ["business_id"], ["id"]
    )
    op.execute(
        """
        CREATE FUNCTION decisions_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Журнал решений только дополняется';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER decisions_append_only BEFORE UPDATE OR DELETE ON decisions
        FOR EACH ROW EXECUTE FUNCTION decisions_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER decisions_append_only ON decisions")
    op.execute("DROP FUNCTION decisions_append_only()")
    op.drop_table("decisions")
