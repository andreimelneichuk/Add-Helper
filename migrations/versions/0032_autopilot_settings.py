"""Настройки автопилота бизнеса и их журнал.

Revision ID: 0032
Revises: 0031
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autopilot_settings",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("allowed_actions", JSONB(), nullable=False),
        sa.Column("cadence", sa.Text(), nullable=False),
        sa.Column("run_hour", sa.Integer(), nullable=False),
        sa.Column("envelope", JSONB(), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        # Когда был последний разбор: по нему расписание не запускает один слот дважды.
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("business_id"),
        sa.CheckConstraint("mode IN ('watch', 'confirm', 'auto')", name="ck_autopilot_mode"),
        sa.CheckConstraint("cadence IN ('daily', 'weekly')", name="ck_autopilot_cadence"),
        sa.CheckConstraint("run_hour BETWEEN 0 AND 23", name="ck_autopilot_run_hour"),
    )
    # Только дополняется: строка истории о смене настроек, площадку она не меняет.
    op.create_table(
        "autopilot_settings_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_autopilot_settings_log_business_id", "autopilot_settings_log", ["business_id", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_autopilot_settings_log_business_id", table_name="autopilot_settings_log")
    op.drop_table("autopilot_settings_log")
    op.drop_table("autopilot_settings")
