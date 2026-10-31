"""Схема очереди Procrastinate, бюджетные периоды, снимки расхода и тревоги.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from procrastinate.schema import SchemaManager
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    connection = op.get_bind()
    # Схема очереди — объект библиотеки: откат её не удаляет, поэтому при повторе пропускаем.
    if connection.scalar(sa.text("SELECT to_regclass('procrastinate_jobs')")) is None:
        # Драйвер разбирает % как параметры, а в схеме библиотеки это обычный текст.
        connection.exec_driver_sql(SchemaManager.get_schema().replace("%", "%%"))

    op.create_table(
        "budget_periods",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hard_limit_micros", sa.BigInteger(), nullable=False),
        sa.Column("reserve_bp", sa.Integer(), nullable=False),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("opening_balance_micros", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("stop_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_budget_periods_state_next_check_at", "budget_periods", ["state", "next_check_at"]
    )
    op.create_table(
        "spend_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("period_id", sa.BigInteger(), sa.ForeignKey("budget_periods.id"), nullable=False),
        sa.Column("reported_micros", sa.BigInteger(), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "measured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "uq_spend_snapshots_period_id_data_as_of",
        "spend_snapshots",
        ["period_id", "data_as_of"],
        unique=True,
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_alerts_open_business_id_kind",
        "alerts",
        ["business_id", "kind"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("spend_snapshots")
    op.drop_table("budget_periods")
