from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.autopilot.adapters.data import SqlAutopilotData, autopilot_accounts
from adhelper.autopilot.adapters.ledgered import LedgeredActions
from adhelper.autopilot.adapters.rules_file import load_rules as load_autopilot_rules
from adhelper.autopilot.adapters.sql import SqlDecisionLog
from adhelper.autopilot.adapters.tests import SqlTestPlanner
from adhelper.autopilot.application.cycle import SCHEDULED_MODE, AutopilotCycle
from adhelper.autopilot.application.visits import WithVisits
from adhelper.budget.adapters.ledgered import (
    LedgeredBudgetPlatform,
    PlatformChangeGateway,
    guard_source,
    limit_proposal,
)
from adhelper.budget.adapters.rules_file import load_rules
from adhelper.budget.adapters.sql import SqlPeriodStore
from adhelper.budget.application.preflight import StoredPeriodPreflight
from adhelper.budget.application.watch_cycle import run_watch_cycle
from adhelper.business.adapters.fetcher import SafeFetcher
from adhelper.business.adapters.rules_file import load_site_rules, load_tag_rules
from adhelper.business.adapters.site_reads import SqlSiteReadQueue
from adhelper.business.adapters.tag_checks import SqlTagCheckQueue, SqlTagChecks
from adhelper.business.application.site import run_site_reads
from adhelper.business.application.tag import run_tag_checks
from adhelper.config import Settings
from adhelper.creatives.adapters.factory import build_text_model
from adhelper.creatives.adapters.moderation_sql import SqlModeration
from adhelper.creatives.adapters.rules_file import (
    load_moderation_rules,
    load_rework_rules,
    load_slot_rules,
)
from adhelper.creatives.adapters.rules_file import load_rules as load_creative_rules
from adhelper.creatives.adapters.sql import (
    SqlAdSlots,
    SqlCreativeQueue,
    SqlKeeperBusinesses,
    SqlProfileSource,
)
from adhelper.creatives.application.moderation import (
    CURRENT_MODERATION_RULES,
    check_moderation,
    send_ready,
)
from adhelper.creatives.application.service import (
    CURRENT_REWORK_RULES,
    CURRENT_RULES,
    CURRENT_SLOT_RULES,
    keep_ads,
    run_queued,
)
from adhelper.db import create_engine
from adhelper.ledger.adapters.sql import (
    PostgresAccountLock,
    SqlChangeRepository,
    accounts_with_open_changes,
)
from adhelper.ledger.application.service import Ledger, reconcile_open_changes
from adhelper.notifications.adapters.money import DashboardMoney
from adhelper.notifications.adapters.smtp import SmtpSender
from adhelper.notifications.adapters.sql import SqlAlerts, SqlOutbox, SqlTelegramLinks
from adhelper.notifications.adapters.telegram import TelegramApi
from adhelper.notifications.application.service import (
    deliver_pending,
    handle_telegram_updates,
)
from adhelper.platforms.adapters.gateway import RoutingGateway

MAX_CHANGE_ATTEMPTS = 3
CURRENT_AUTOPILOT_RULES = "autopilot-2"
CURRENT_EXPERIMENT_RULES = "experiments-1"


async def _increase_needs_owner(account: str, campaign_id: str, new_limit: int) -> str:
    # Лимит автопилот сейчас только предлагает: см. SCHEDULED_MODE.
    return "awaiting_confirmation"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Runtime:
    """Точка сборки фоновых задач."""

    sessions: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    # Адаптер настоящей площадки появится в T5; пока площадки нет, бизнес получает тревогу.
    platform_for: Callable[[UUID, str], Any]
    clock: Callable[[], datetime] = _utc_now
    # Без ключа или модели генерация отдаёт шаблоны из профиля, без судьи — только проверку кодом.
    text_model: Any = None
    judge_model: Any = None
    # Имя модели пишется в каждый разбор сайта: по нему сравниваем извлекатели.
    text_model_name: str = ""
    # Каналы тревог; без настроек канала отправки ждут в очереди.
    email: Any = None
    telegram: Any = None
    cabinet_url: str = "http://localhost:8000"
    # Визиты Метрики (T6); без источника проверки визитов в автопилоте пропускаются.
    visits_for: Callable[[UUID], Any] = lambda _business_id: None

    def ledger_for(self, business_id: UUID, account: str) -> Ledger:
        platform = self.platform_for(business_id, account)
        if platform is None:
            raise LookupError("Площадка не подключена")
        return Ledger(
            repository=SqlChangeRepository(self.sessions, business_id),
            # Просьбы владельца об объявлениях (ad:) повторяются здесь же, если Яндекс не ответил.
            # Создание кампаний и объявлений доводит до конца и согласование после сбоя.
            gateway=RoutingGateway(
                PlatformChangeGateway(platform),
                platform,
                platform,
                launcher=platform,
                publisher=platform,
            ),
            lock=PostgresAccountLock(self.engine),
            max_attempts=MAX_CHANGE_ATTEMPTS,
            preflight=StoredPeriodPreflight(
                SqlPeriodStore(self.sessions), platform, business_id, self.clock, load_rules
            ),
        )

    def limit_proposal(self, account: str, campaign_id: str, new_limit: int) -> Any:
        return limit_proposal(account, guard_source(), campaign_id, new_limit)

    def _guarded_platform(self, business_id: UUID, account: str) -> LedgeredBudgetPlatform | None:
        platform = self.platform_for(business_id, account)
        if platform is None:
            return None
        return LedgeredBudgetPlatform(
            platform, self.ledger_for(business_id, account), guard_source()
        )

    async def budget_watch(self) -> None:
        await run_watch_cycle(
            SqlPeriodStore(self.sessions),
            self._guarded_platform,
            SqlAlerts.factory(self.sessions),
            self.clock(),
            load_rules,
        )

    async def state_reconcile(self) -> None:
        def ledger_or_none(business_id: UUID, account: str) -> Ledger | None:
            if self.platform_for(business_id, account) is None:
                return None
            return self.ledger_for(business_id, account)

        await reconcile_open_changes(
            await accounts_with_open_changes(self.sessions), ledger_or_none
        )

    async def creatives_generate(self) -> None:
        await run_queued(
            SqlCreativeQueue(self.sessions),
            SqlProfileSource(self.sessions),
            self.text_model,
            self.judge_model,
            load_creative_rules,
            self.clock,
            load_rework=load_rework_rules,
        )

    async def site_read(self) -> None:
        rules = load_site_rules()
        await run_site_reads(
            SqlSiteReadQueue(self.sessions),
            SafeFetcher(rules),
            self.text_model,
            self.text_model_name,
            rules,
            load_site_rules,
            self.clock,
        )

    async def site_tag_check(self) -> None:
        await run_tag_checks(
            SqlTagCheckQueue(self.sessions),
            SafeFetcher(load_site_rules()),
            load_tag_rules(),
            self.clock,
        )

    async def ads_keep(self) -> None:
        profiles = SqlProfileSource(self.sessions)
        slot_rules = load_slot_rules(CURRENT_SLOT_RULES)
        creative_rules = load_creative_rules(CURRENT_RULES)
        for business_id in await SqlKeeperBusinesses(self.sessions).business_ids():
            await keep_ads(
                business_id,
                SqlAdSlots(self.sessions, business_id),
                profiles,
                SqlAlerts(self.sessions, business_id),
                slot_rules,
                creative_rules,
                self.clock(),
            )

    async def ads_moderate(self, only: UUID | None = None) -> None:
        """Отправка занявших место объявлений и чтение итога проверки Яндекса."""
        rules = load_moderation_rules(CURRENT_MODERATION_RULES)
        for business_id in await SqlKeeperBusinesses(self.sessions).business_ids():
            if only is not None and business_id != only:
                continue

            def ledger_for(account: str, business_id: UUID = business_id) -> Ledger | None:
                if self.platform_for(business_id, account) is None:
                    return None
                return self.ledger_for(business_id, account)

            def publisher_for(account: str, business_id: UUID = business_id) -> Any:
                return self.platform_for(business_id, account)

            store = SqlModeration(self.sessions, business_id)
            await send_ready(store, ledger_for, self.clock())
            await check_moderation(
                store,
                publisher_for,
                ledger_for,
                SqlAlerts(self.sessions, business_id),
                rules,
                CURRENT_RULES,
                CURRENT_REWORK_RULES,
                self.clock(),
            )

    def autopilot_cycle(self, business_id: UUID, account: str) -> AutopilotCycle:
        rules = load_autopilot_rules(CURRENT_AUTOPILOT_RULES)
        data: Any = SqlAutopilotData(self.sessions, business_id, rules)
        visits = self.visits_for(business_id)
        if visits is not None:
            counter = SqlTagChecks(self.sessions, business_id).counter
            data = WithVisits(data, visits, counter, rules)
        return AutopilotCycle(
            data,
            SqlDecisionLog(self.sessions, business_id),
            LedgeredActions(self.ledger_for(business_id, account), _increase_needs_owner),
            SqlAlerts(self.sessions, business_id),
            self.clock,
            rules,
            SqlTestPlanner(self.sessions, business_id, CURRENT_EXPERIMENT_RULES),
        )

    async def autopilot_run(self, only: UUID | None = None) -> int:
        """Раз в сутки: анализ по отчётам и предложения владельцу. Возвращает число кабинетов."""
        done = 0
        for business_id, account, goal in await autopilot_accounts(self.sessions):
            if only is not None and business_id != only:
                continue
            if self.platform_for(business_id, account) is None:
                continue
            await self.autopilot_cycle(business_id, account).run(account, goal, SCHEDULED_MODE)
            done += 1
        return done

    async def notifications_send(self) -> None:
        senders = {
            channel: sender
            for channel, sender in (("email", self.email), ("telegram", self.telegram))
            if sender is not None
        }
        await deliver_pending(
            SqlOutbox(self.sessions),
            senders,
            DashboardMoney(self.sessions),
            self.clock(),
            self.cabinet_url,
        )

    async def telegram_poll(self) -> None:
        if self.telegram is not None:
            await handle_telegram_updates(
                self.telegram, SqlTelegramLinks(self.sessions), self.clock()
            )


def _no_platform(business_id: UUID, account: str) -> None:
    return None


def _model(settings: Settings, name: str) -> Any:
    return build_text_model(
        settings.text_model_base_url,
        settings.text_model_api_key,
        name,
        settings.text_model_project,
    )


def _email(settings: Settings) -> SmtpSender | None:
    if not settings.smtp_host or not settings.smtp_from:
        return None
    password = settings.smtp_password.get_secret_value() if settings.smtp_password else ""
    return SmtpSender(
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_from,
        settings.smtp_username,
        password,
        settings.smtp_starttls,
    )


def _telegram(settings: Settings) -> TelegramApi | None:
    token = settings.telegram_bot_token
    if token is None or not token.get_secret_value():
        return None
    return TelegramApi(token.get_secret_value())


@asynccontextmanager
async def open_runtime(settings: Settings) -> AsyncIterator[Runtime]:
    engine = create_engine(settings)
    try:
        yield Runtime(
            sessions=async_sessionmaker(engine, expire_on_commit=False),
            engine=engine,
            platform_for=_no_platform,
            text_model=_model(settings, settings.text_model_name),
            text_model_name=settings.text_model_name,
            judge_model=_model(settings, settings.judge_model_name),
            email=_email(settings),
            telegram=_telegram(settings),
            cabinet_url=settings.public_base_url,
        )
    finally:
        await engine.dispose()
