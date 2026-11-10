"""Ответ владельца, сколько обращений стали клиентами, цена клиента.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Один ответ на бизнес и окно дат; повторный ответ за то же окно заменяет прежний.
    op.create_table(
        "client_answers",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("since", sa.Date(), nullable=False),
        sa.Column("until", sa.Date(), nullable=False),
        sa.Column("leads", sa.Integer(), nullable=False),
        sa.Column("clients", sa.Integer(), nullable=False),
        sa.Column(
            "answered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("business_id", "since", "until"),
        sa.CheckConstraint("clients >= 0 AND clients <= leads", name="ck_client_answers_clients"),
    )


def downgrade() -> None:
    op.drop_table("client_answers")
