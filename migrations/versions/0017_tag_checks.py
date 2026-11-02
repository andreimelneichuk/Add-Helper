"""Проверки кода Метрики на сайте владельца: строка служит заданием воркера.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "tag_checks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "counters",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("problem", sa.Text(), nullable=True),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'found', 'missing', 'failed')",
            name="ck_tag_checks_status",
        ),
    )
    op.create_index("ix_tag_checks_business_created", "tag_checks", ["business_id", "created_at"])
    op.create_index(
        "ix_tag_checks_waiting",
        "tag_checks",
        ["created_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_table("tag_checks")
