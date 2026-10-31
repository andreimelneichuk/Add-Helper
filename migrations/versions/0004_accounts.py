"""Пользователи, бизнесы, кабинеты, сессии, потоки OAuth и внешние ключи.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None

_ts = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("yandex_uid", sa.Text(), nullable=False, unique=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("consent_at", _ts, nullable=True),
        sa.Column("created_at", _ts, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "businesses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", _ts, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_businesses_user_id", "businesses", ["user_id"])
    op.create_table(
        "ad_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("external_login", sa.Text(), nullable=False),
        sa.Column("encrypted_access", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh", sa.Text(), nullable=False),
        sa.Column("token_expires_at", _ts, nullable=False),
        sa.Column("token_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("scopes", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.UniqueConstraint("business_id", "external_login", name="uq_ad_accounts_business_login"),
    )
    op.create_index(
        "uq_ad_accounts_active_platform_login",
        "ad_accounts",
        ["platform", "external_login"],
        unique=True,
        postgresql_where=sa.text("state <> 'disconnected'"),
    )
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("expires_at", _ts, nullable=False),
        sa.Column("revoked_at", _ts, nullable=True),
    )
    op.create_table(
        "oauth_flows",
        sa.Column("state_hash", sa.Text(), primary_key=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("encrypted_verifier", sa.Text(), nullable=False),
        sa.Column("expires_at", _ts, nullable=False),
        sa.Column("used_at", _ts, nullable=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id"), nullable=True),
    )
    # Кабинет принадлежит бизнесу: журнал и период не могут сослаться на чужой.
    op.create_foreign_key(
        "fk_changes_business_account",
        "changes",
        "ad_accounts",
        ["business_id", "account"],
        ["business_id", "external_login"],
    )
    op.create_foreign_key(
        "fk_budget_periods_business_account",
        "budget_periods",
        "ad_accounts",
        ["business_id", "account"],
        ["business_id", "external_login"],
    )
    op.create_foreign_key("fk_alerts_business", "alerts", "businesses", ["business_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_alerts_business", "alerts")
    op.drop_constraint("fk_budget_periods_business_account", "budget_periods")
    op.drop_constraint("fk_changes_business_account", "changes")
    op.drop_table("oauth_flows")
    op.drop_table("sessions")
    op.drop_table("ad_accounts")
    op.drop_table("businesses")
    op.drop_table("users")
