"""Захват исхода воркером: состояние «оценивается» и срок аренды.

Два воркера не должны оценивать и откатывать один исход. Захваченная запись держится
`claimed_at`; если воркер упал, по истечении аренды запись захватывают заново.

Revision ID: 0037
Revises: 0036
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels = None
depends_on = None

OLD = "status IN ('pending', 'evaluated', 'skipped')"
NEW = "status IN ('pending', 'evaluating', 'evaluated', 'skipped')"


def upgrade() -> None:
    op.add_column(
        "decision_outcomes", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_constraint("ck_decision_outcomes_status", "decision_outcomes", type_="check")
    op.create_check_constraint("ck_decision_outcomes_status", "decision_outcomes", NEW)


def downgrade() -> None:
    op.execute(
        "UPDATE decision_outcomes SET status = 'pending', claimed_at = NULL"
        " WHERE status = 'evaluating'"
    )
    op.drop_constraint("ck_decision_outcomes_status", "decision_outcomes", type_="check")
    op.create_check_constraint("ck_decision_outcomes_status", "decision_outcomes", OLD)
    op.drop_column("decision_outcomes", "claimed_at")
