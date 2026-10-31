"""Журнал изменений: только дополняется, снимок «до» и план закрыты триггером.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None

GUARD_FUNCTION = """
CREATE FUNCTION changes_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'Журнал changes только дополняется: % запрещён', TG_OP;
    END IF;
    IF (NEW.id, NEW.business_id, NEW.account, NEW.source, NEW.decision_id, NEW.kind,
        NEW.target_ref, NEW.before, NEW.planned, NEW.explanation, NEW.reverts_change_id,
        NEW.idempotency_key, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.business_id, OLD.account, OLD.source, OLD.decision_id, OLD.kind,
        OLD.target_ref, OLD.before, OLD.planned, OLD.explanation, OLD.reverts_change_id,
        OLD.idempotency_key, OLD.created_at) THEN
        RAISE EXCEPTION 'Неизменяемые поля журнала changes нельзя менять';
    END IF;
    IF OLD.status NOT IN ('planned', 'applying') THEN
        RAISE EXCEPTION 'Завершённую запись журнала changes нельзя менять';
    END IF;
    IF NEW.status = 'planned' OR NEW.status = 'rejected' THEN
        RAISE EXCEPTION 'Недопустимый переход статуса: % -> %', OLD.status, NEW.status;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.create_table(
        "changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("decision_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("target_ref", sa.Text(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("planned", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("reverts_change_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "uq_changes_open_idempotency_key",
        "changes",
        ["business_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('planned', 'applying')"),
    )
    op.create_index(
        "ix_changes_business_id_account_status", "changes", ["business_id", "account", "status"]
    )
    op.execute(GUARD_FUNCTION)
    op.execute(
        "CREATE TRIGGER changes_guard_rows BEFORE UPDATE OR DELETE ON changes "
        "FOR EACH ROW EXECUTE FUNCTION changes_guard()"
    )
    op.execute(
        "CREATE TRIGGER changes_guard_truncate BEFORE TRUNCATE ON changes "
        "FOR EACH STATEMENT EXECUTE FUNCTION changes_guard()"
    )


def downgrade() -> None:
    op.drop_table("changes")
    op.execute("DROP FUNCTION changes_guard()")
