"""Данные рекламодателя для маркировки: тип, ИНН, имя, телефон, ОКВЭД.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ИНН, имя и телефон только шифртекстом: контекст шифра связан с бизнесом.
    op.create_table(
        "advertiser_marking",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("encrypted_inn", sa.Text(), nullable=False),
        sa.Column("encrypted_name", sa.Text(), nullable=False),
        sa.Column("encrypted_phone", sa.Text(), nullable=True),
        # Код вида деятельности открытый, в отличие от ИНН и ФИО.
        sa.Column("okved", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("business_id"),
        sa.CheckConstraint("kind IN ('legal', 'ip', 'person')", name="ck_advertiser_marking_kind"),
    )


def downgrade() -> None:
    op.drop_table("advertiser_marking")
