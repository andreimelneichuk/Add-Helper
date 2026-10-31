from alembic import context
from sqlalchemy import create_engine

from adhelper.config import Settings
from adhelper.db import Base
from adhelper.ledger.adapters import sql as _ledger_models  # noqa: F401


def run_migrations() -> None:
    engine = create_engine(Settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
