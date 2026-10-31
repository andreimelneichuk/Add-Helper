from procrastinate import App, PsycopgConnector

from adhelper.config import Settings
from adhelper.worker.runtime import open_runtime

BUDGET_QUEUE = "budget"
PERIODIC_TASKS = ("budget.watch", "state.reconcile")


def connector_for(settings: Settings) -> PsycopgConnector:
    return PsycopgConnector(conninfo=settings.postgres_dsn)


app = App(connector=connector_for(Settings()))


# Каждые 5 минут: сам период решает, пора ли его проверять (next_check_at).
@app.periodic(cron="*/5 * * * *")
@app.task(name="budget.watch", queue=BUDGET_QUEUE, queueing_lock="budget.watch")
async def budget_watch(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.budget_watch()


@app.periodic(cron="*/5 * * * *")
@app.task(name="state.reconcile", queue=BUDGET_QUEUE, queueing_lock="state.reconcile")
async def state_reconcile(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.state_reconcile()
