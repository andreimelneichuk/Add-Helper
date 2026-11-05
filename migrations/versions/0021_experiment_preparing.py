"""Тест готовится до появления варианта: вариант пишется и проходит модерацию позже.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column("experiments", "variant_ref", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Старые версии ждут вариант у каждого теста: готовящиеся без варианта отменяем.
    op.execute(
        "UPDATE experiments SET variant_ref = '', status = 'cancelled' WHERE variant_ref IS NULL"
    )
    op.alter_column("experiments", "variant_ref", existing_type=sa.Text(), nullable=False)
