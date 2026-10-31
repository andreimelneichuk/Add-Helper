from typing import Any

from alembic import context
from sqlalchemy import create_engine

from adhelper.accounts.adapters import sql as _account_models  # noqa: F401
from adhelper.autopilot.adapters import sql as _autopilot_models  # noqa: F401
from adhelper.budget.adapters import sql as _budget_models  # noqa: F401
from adhelper.config import Settings
from adhelper.db import Base
from adhelper.ledger.adapters import sql as _ledger_models  # noqa: F401
from adhelper.notifications.adapters import sql as _notification_models  # noqa: F401

CROSS_MODULE_KEYS = {
    "fk_alerts_business",
    "fk_budget_periods_business_account",
    "fk_changes_business_account",
    "fk_decisions_business",
}


def _only_our_tables(
    _: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Таблицы очереди принадлежат Procrastinate и в наших моделях не описаны.

    Ключи между модулями описаны только в миграциях: модели не знают соседние модули.
    """
    if type_ == "foreign_key_constraint" and name in CROSS_MODULE_KEYS:
        return False
    return not (type_ == "table" and reflected and compare_to is None)


def run_migrations() -> None:
    engine = create_engine(Settings().database_url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            include_object=_only_our_tables,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
