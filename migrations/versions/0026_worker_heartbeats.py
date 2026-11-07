"""Сердцебиение фоновых воркеров для проверки здоровья сервиса.

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Отметка времени последнего выполнения задач фоновыми воркерами.
    # Если отметка отстаёт более чем на 5 минут, /healthz сообщает о сбое.
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_name", sa.Text(), nullable=False),
        sa.Column(
            "last_heartbeat",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("worker_name", name="pk_worker_heartbeats"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
