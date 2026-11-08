"""Разрешить одному владельцу использовать кабинет в нескольких бизнесах.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ad_account_owners",
        sa.Column("platform", sa.Text(), primary_key=True),
        sa.Column("external_login", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.execute(
        "INSERT INTO ad_account_owners (platform, external_login, user_id) "
        "SELECT DISTINCT ON (a.platform, a.external_login) a.platform, a.external_login, b.user_id "
        "FROM ad_accounts AS a JOIN businesses AS b ON b.id = a.business_id "
        "WHERE a.state <> 'disconnected' ORDER BY a.platform, a.external_login, a.id"
    )
    op.drop_index("uq_ad_accounts_active_platform_login", table_name="ad_accounts")
    op.create_index(
        "ix_ad_accounts_platform_login_active",
        "ad_accounts",
        ["platform", "external_login"],
        postgresql_where=sa.text("state <> 'disconnected'"),
    )


def downgrade() -> None:
    op.drop_index("ix_ad_accounts_platform_login_active", table_name="ad_accounts")
    op.create_index(
        "uq_ad_accounts_active_platform_login",
        "ad_accounts",
        ["platform", "external_login"],
        unique=True,
        postgresql_where=sa.text("state <> 'disconnected'"),
    )
    op.drop_table("ad_account_owners")
