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
    "experiments.stats",
    "ads.summary",
    "cleanup.jobs",
    "worker.heartbeat",
    "worker.heartbeat.creatives",
)


def connector_for(settings: Settings) -> PsycopgConnector:
    return PsycopgConnector(conninfo=settings.postgres_dsn_unpooled)


app = App(connector=connector_for(Settings()))


# Каждые 5 минут: сам период решает, пора ли его проверять (next_check_at).
@app.periodic(cron="*/5 * * * *")
@app.task(name="budget.watch", queue=BUDGET_QUEUE, queueing_lock="budget.watch")
async def budget_watch(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.worker_heartbeat("budget")
        await runtime.budget_watch()


@app.periodic(cron="*/5 * * * *")
@app.task(name="state.reconcile", queue=BUDGET_QUEUE, queueing_lock="state.reconcile")
async def state_reconcile(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.worker_heartbeat("budget")
        await runtime.state_reconcile()


# Очередь наборов — строки creative_sets; задача разбирает их каждые 5 секунд.
@app.periodic(cron="* * * * * */5")
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
# Очередь креативов: может генерировать картинки через модель.
@app.periodic(cron="*/5 * * * *")
@app.task(name="ads.moderate", queue=CREATIVES_QUEUE, queueing_lock="ads.moderate")
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


# Разборы сайта — строки site_reads, их ставит владелец; задача разбирает их каждые 5 секунд.
# Секунды в cron у croniter идут последним полем: `*/5 * * * * *` значило бы «каждую секунду
# каждой пятой минуты», и разбор ждал бы до пяти минут.
@app.periodic(cron="* * * * * */5")
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
@app.task(name="autopilot.run", queue=CREATIVES_QUEUE, queueing_lock="autopilot.run")
async def autopilot_run(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.autopilot_run()


# Ход A/B-тестов: вариант через журнал, проверка Яндекса, одновременный показ и итог.
# Раз в час: повтор ничего не меняет, переключение начинается с чтения.
@app.periodic(cron="7 * * * *")
@app.task(name="experiments.run", queue=CREATIVES_QUEUE, queueing_lock="experiments.run")
async def experiments_run(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.experiments_run()


# Отчёт по объявлениям идущих тестов за вчера: 06:05 по Москве (03:05 UTC), до суточного
# цикла автопилота. Повтор заменяет строки за те же сутки.
@app.periodic(cron="5 3 * * *")
@app.task(name="experiments.stats", queue=CREATIVES_QUEUE, queueing_lock="experiments.stats")
async def experiments_stats(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.experiments_stats()


# Сводка «что запущено» за вчерашние московские сутки, в 10:00 по Москве (07:00 UTC).
# Повтор за тот же день письма не задваивает: сводка пишется один раз на день.
@app.periodic(cron="0 7 * * *")
@app.task(name="ads.summary", queue=NOTIFICATIONS_QUEUE, queueing_lock="ads.summary")
async def ads_summary(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.ads_summary()


# Очистка выполненных и отменённых задач старше 7 дней: раз в сутки в 04:00 UTC.
@app.periodic(cron="0 4 * * *")
@app.task(name="cleanup.jobs", queue=CREATIVES_QUEUE, queueing_lock="cleanup.jobs")
async def cleanup_jobs(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.cleanup_jobs(older_than_days=7)


# Сердцебиение воркеров: раз в минуту для проверки здоровья в /healthz.
@app.periodic(cron="* * * * *")
@app.task(name="worker.heartbeat", queue=NOTIFICATIONS_QUEUE, queueing_lock="worker.heartbeat")
async def worker_heartbeat(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.worker_heartbeat("notifications")


@app.periodic(cron="* * * * *")
@app.task(
    name="worker.heartbeat.creatives",
    queue=CREATIVES_QUEUE,
    queueing_lock="worker.heartbeat.creatives",
)
async def worker_heartbeat_creatives(timestamp: int) -> None:
    async with open_runtime(Settings()) as runtime:
        await runtime.worker_heartbeat("creatives")
