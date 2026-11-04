from procrastinate import App, PsycopgConnector

from adhelper.config import Settings
from adhelper.worker.runtime import open_runtime

BUDGET_QUEUE = "budget"
# Отдельная очередь: долгий вызов модели не задерживает задачи бюджета.
CREATIVES_QUEUE = "creatives"
# Тревоги владельцу не ждут ни генерации, ни задач бюджета.
NOTIFICATIONS_QUEUE = "notifications"
PERIODIC_TASKS = (
    "budget.watch",
    "state.reconcile",
    "creatives.generate",
    "ads.keep",
    "ads.moderate",
    "notifications.send",
    "telegram.poll",
    "site.read",
    "site.tag_check",
    "autopilot.run",
    "experiments.run",
)


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


# Очередь наборов — строки creative_sets; задача только разбирает их раз в минуту.
@app.periodic(cron="* * * * *")
@app.task(name="creatives.generate", queue=CREATIVES_QUEUE, queueing_lock="creatives.generate")
async def creatives_generate(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.creatives_generate()


# Хранитель объявлений: сам решает, когда писать и что ставить на место (docs/05 §8).
@app.periodic(cron="*/5 * * * *")
@app.task(name="ads.keep", queue=CREATIVES_QUEUE, queueing_lock="ads.keep")
async def ads_keep(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.ads_keep()


# Отправка занявших место объявлений через журнал и итог проверки Яндекса (docs/05 §8).
# Очередь бюджета: здесь нет вызовов модели, а журнал кабинета общий с задачами бюджета.
@app.periodic(cron="*/5 * * * *")
@app.task(name="ads.moderate", queue=BUDGET_QUEUE, queueing_lock="ads.moderate")
async def ads_moderate(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.ads_moderate()


# Отправки тревог — строки notification_deliveries, их создаёт тревога в той же транзакции.
@app.periodic(cron="* * * * *")
@app.task(name="notifications.send", queue=NOTIFICATIONS_QUEUE, queueing_lock="notifications.send")
async def notifications_send(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.notifications_send()


@app.periodic(cron="* * * * *")
@app.task(name="telegram.poll", queue=NOTIFICATIONS_QUEUE, queueing_lock="telegram.poll")
async def telegram_poll(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.telegram_poll()


# Разборы сайта — строки site_reads, их ставит владелец; задача разбирает их раз в минуту.
@app.periodic(cron="* * * * *")
@app.task(name="site.read", queue=CREATIVES_QUEUE, queueing_lock="site.read")
async def site_read(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.site_read()


# Проверки кода Метрики — строки tag_checks; скачивается только главная страница.
@app.periodic(cron="* * * * *")
@app.task(name="site.tag_check", queue=CREATIVES_QUEUE, queueing_lock="site.tag_check")
async def site_tag_check(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.site_tag_check()


# Раз в сутки после ночной сверки отчётов: 06:20 по Москве, отчёты за вчера уже есть.
@app.periodic(cron="20 3 * * *")
@app.task(name="autopilot.run", queue=BUDGET_QUEUE, queueing_lock="autopilot.run")
async def autopilot_run(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.autopilot_run()


# Ход A/B-тестов: вариант через журнал, проверка Яндекса, чередование по дням и итог.
# Раз в час: переключение идёт по московским суткам, повтор внутри суток ничего не меняет.
@app.periodic(cron="7 * * * *")
@app.task(name="experiments.run", queue=BUDGET_QUEUE, queueing_lock="experiments.run")
async def experiments_run(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.experiments_run()
