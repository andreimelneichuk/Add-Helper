"""Состояние «выполняется» у переносов: доводчик захватывает запись, а не читает её.

Revision ID: 0035
Revises: 0034
"""

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels = None
depends_on = None

OLD = "status IN ('open', 'done', 'rejected', 'rolled_back', 'stuck', 'cancelled')"
NEW = "status IN ('open', 'running', 'done', 'rejected', 'rolled_back', 'stuck', 'cancelled')"


def upgrade() -> None:
    op.drop_constraint("ck_autopilot_transfers_status", "autopilot_transfers", type_="check")
    op.create_check_constraint("ck_autopilot_transfers_status", "autopilot_transfers", NEW)


def downgrade() -> None:
    op.execute("UPDATE autopilot_transfers SET status = 'open' WHERE status = 'running'")
    op.drop_constraint("ck_autopilot_transfers_status", "autopilot_transfers", type_="check")
    op.create_check_constraint("ck_autopilot_transfers_status", "autopilot_transfers", OLD)
