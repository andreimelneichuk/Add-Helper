"""Картинки объявлений: свои фото и сгенерированные сцены, номера картинок на площадке.

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "creative_images",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        # SHA-256 готового JPEG: по нему журнал сверяет картинку объявления.
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        # Направление (кампания), для которого сгенерирована сцена; у своих фото пусто.
        sa.Column("account", sa.Text(), nullable=True),
        sa.Column("campaign_id", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("prompt_source", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=True),
        # Владелец подтвердил: людей на фото нет или у них есть согласие.
        sa.Column("consent", sa.Boolean(), nullable=False, server_default="false"),
        # Картинки не удаляются, а убираются: объявление с ней ещё может работать.
        sa.Column("state", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], name="fk_creative_images_business"
        ),
        sa.UniqueConstraint("business_id", "sha256", name="uq_creative_images_sha256"),
    )
    op.create_index(
        "ix_creative_images_business_state", "creative_images", ["business_id", "state", "id"]
    )
    op.create_table(
        "platform_images",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        # AdImageHash Директа: одна и та же картинка второй раз не загружается.
        sa.Column("image_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("business_id", "account", "sha256", name="pk_platform_images"),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], name="fk_platform_images_business"
        ),
    )
    op.create_index(
        "ix_platform_images_hash", "platform_images", ["business_id", "account", "image_hash"]
    )
    op.add_column("creatives", sa.Column("image_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_creatives_image", "creatives", "creative_images", ["image_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_creatives_image", "creatives", type_="foreignkey")
    op.drop_column("creatives", "image_id")
    op.drop_index("ix_platform_images_hash", table_name="platform_images")
    op.drop_table("platform_images")
    op.drop_index("ix_creative_images_business_state", table_name="creative_images")
    op.drop_table("creative_images")
