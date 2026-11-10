"""Лестница доверия автопилота: открытые классы действий и начало самостоятельного режима.

Значение по умолчанию совпадает с поведением до этапа 9: самому можно только паузу объявления
и тест. Старый код колонки не читает, поэтому миграция обратно совместима (этап 9).

Revision ID: 0039
Revises: 0038
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "autopilot_settings",
        sa.Column(
            "granted_classes",
            JSONB(),
            nullable=False,
            server_default=sa.text("""'["pause_ad", "start_test"]'::jsonb"""),
        ),
    )
    op.add_column(
        "autopilot_settings", sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "autopilot_settings", sa.Column("auto_since", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("autopilot_settings", "auto_since")
    op.drop_column("autopilot_settings", "granted_at")
    op.drop_column("autopilot_settings", "granted_classes")
