"""Рост лимита открыт у кабинетов по умолчанию: решение владельца проекта 2026-11-10.

Добавляет `increase_limit` в разрешённые действия у уже сохранённых настроек; класс
`shift_limit` цикл считает открытым всегда (`domain/trust.py`). Рамки кабинета и политика
денег не меняются.

Revision ID: 0041
Revises: 0040
"""

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE autopilot_settings SET allowed_actions = allowed_actions "
        "|| '[\"increase_limit\"]'::jsonb"
        " WHERE NOT allowed_actions @> '[\"increase_limit\"]'::jsonb"
    )


def downgrade() -> None:
    # Добавленное действие безвредно: без открытого класса оно останется карточкой.
    pass
