"""Allow an owner-requested replacement to explicitly omit its image.

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "creatives",
        sa.Column("image_disabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("creatives", "image_disabled")
