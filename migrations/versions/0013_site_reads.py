"""Разборы сайта в черновик профиля, извлекатель.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None

STATUSES = "'queued', 'running', 'ready', 'failed'"


def upgrade() -> None:
    # Строка — и очередь воркера, и результат. Текст сайта не храним, только факты с цитатами.
    op.create_table(
        "site_reads",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("problem", sa.Text(), nullable=True),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "facts", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "dropped", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "pages", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_site_reads_status"),
    )
    op.create_index("ix_site_reads_business", "site_reads", ["business_id", "created_at"])
    op.create_index(
        "ix_site_reads_waiting",
        "site_reads",
        ["created_at"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_table("site_reads")
