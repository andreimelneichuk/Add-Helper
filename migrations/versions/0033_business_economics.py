"""Экономика бизнеса: из неё считается допустимая цена обращения.

Revision ID: 0033
Revises: 0032
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_economics",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        # Средний чек, который назвал владелец. Пусто: берём среднюю цену услуг из профиля.
        sa.Column("avg_check_micros", sa.BigInteger(), nullable=True),
        sa.Column("lead_to_client_bp", sa.Integer(), nullable=False),
        sa.Column("margin_bp", sa.Integer(), nullable=False),
        sa.Column("repeat_bp", sa.Integer(), nullable=False),
        sa.Column("ad_share_bp", sa.Integer(), nullable=False),
        sa.Column("capacity_per_week", sa.Integer(), nullable=True),
        # Пусто: значения остались ориентиром, автопилот по ним выше «Предлагает» не идёт.
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("business_id"),
        sa.CheckConstraint("lead_to_client_bp BETWEEN 1 AND 10000", name="ck_econ_lead"),
        sa.CheckConstraint("margin_bp BETWEEN 1 AND 10000", name="ck_econ_margin"),
        sa.CheckConstraint("repeat_bp BETWEEN 0 AND 30000", name="ck_econ_repeat"),
        sa.CheckConstraint("ad_share_bp BETWEEN 1 AND 10000", name="ck_econ_share"),
        sa.CheckConstraint(
            "avg_check_micros IS NULL OR avg_check_micros > 0", name="ck_econ_check"
        ),
        sa.CheckConstraint(
            "capacity_per_week IS NULL OR capacity_per_week > 0", name="ck_econ_capacity"
        ),
    )


def downgrade() -> None:
    op.drop_table("business_economics")
