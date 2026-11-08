"""Добавление soft-delete (deleted_at) для бизнесов.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "businesses",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_businesses_user_id_active",
        "businesses",
        ["user_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_businesses_user_id_active", table_name="businesses")
    op.drop_column("businesses", "deleted_at")
