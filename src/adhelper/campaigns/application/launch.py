"""Запуск рекламы из мастера: период бюджета и кампании, тем же путём, что любое изменение.

План → период бюджета и строка запуска одной транзакцией → журнал со снимком «до» →
исполнитель → чтение результата. Повтор продолжает начатое: созданная кампания находится
по метке и второй раз не создаётся.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from adhelper.budget.domain.calendar import MOSCOW as MOSCOW_TZ
from adhelper.budget.domain.calendar import moscow_period, platform_date
from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.budget.domain.rules import BudgetRules
from adhelper.campaigns.domain.keywords import (
    AUTOTARGETING_KEYWORD,
    KEYWORDS_EXPANSION_SCHEMA,
    KEYWORDS_EXPANSION_SYSTEM,
    generate_keywords,
    parse_synonyms_response,
)
from adhelper.campaigns.domain.launch import (
    LAUNCH_PREFIX,
    MICROS_PER_RUB,
    PLACEMENT_WORDS,
    LaunchPlan,
    LaunchProblem,
    LaunchRules,
    NewAd,
    NewCampaign,
    UnregisteredAdvertiserError,
    plan_launch,
    region_of,
)
from adhelper.campaigns.domain.ports import ActivePeriodExists, LaunchStore, StoredLaunch
from adhelper.creatives.domain.model import TextModel
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.failures import failure_text
from adhelper.ledger.domain.model import (
    Change,
    ChangeKind,
    ChangeSource,
    ChangeStatus,
    ProposedChange,
)
from adhelper.ledger.domain.ports import GatewayUnavailable
from adhelper.platforms.domain.ports import CLIENT_PREFIX, Advertiser

logger = logging.getLogger(__name__)

# Типы домена нужны HTTP-слою, который сам домен не импортирует.
__all__ = [
    "AUTOTARGETING_KEYWORD",
    "MOSCOW_TZ",
    "PLACEMENT_WORDS",
    "Advertiser",
    "LaunchInputs",
    "LaunchPlan",
    "LaunchProblem",
    "LaunchResult",
    "LaunchStatus",
    "LaunchedCampaign",
    "NewAd",
    "UnregisteredAdvertiserError",
    "expand_keywords_with_model",
    "generate_keywords",
    "launch",
    "launch_state",
    "preview",
    "region_of",
]


class LaunchStatus(StrEnum):
    DONE = "done"
    PENDING = "pending"
    REFUSED = "refused"
    PROBLEM = "problem"


@dataclass(frozen=True)
class LaunchInputs:
    title: str
    account: str
    monthly: int
    ads: tuple[NewAd, ...]
    href: str
    address: str
    counters: tuple[int, ...]
    keywords: tuple[str, ...] = ()
    region_id: int | None = None
    city_name: str = ""


@dataclass(frozen=True)
class LaunchedCampaign:
    campaign_id: str
    name: str
    limit: int
    href: str
    ads: tuple[NewAd, ...]
    ad_ids: tuple[str, ...]


@dataclass(frozen=True)
class LaunchResult:
    status: LaunchStatus
    created: tuple[LaunchedCampaign, ...] = field(default_factory=tuple)
    problem: LaunchProblem | None = None
    minimum_monthly: int | None = None
    # Почему журнал не дал создать: причина проверки бюджета или «уже идёт период».
    reason: str | None = None
    ends_at: datetime | None = None


def _rub(micros: int) -> str:
    return f"{micros // MICROS_PER_RUB:,}".replace(",", " ") + " ₽"


def _explanation(campaign: NewCampaign) -> str:
    ads = len(campaign.ads)
    until = campaign.ends_at.astimezone(MOSCOW_TZ).strftime("%d.%m.%Y")
    return (
        f"Создал кампанию «{campaign.name}»: {PLACEMENT_WORDS[campaign.placement]},"
        f" лимит {_rub(campaign.limit)} до {until}, объявлений {ads}."
        " Почему: вы запустили рекламу в мастере, лимит стоит в самом Яндексе."
        " Дальше: Яндекс проверит объявления, показы начнутся после проверки."
        " Отменить: пауза в шапке кабинета останавливает показы, кампания остаётся."
    )


def _plan(key: str, inputs: LaunchInputs, period: BudgetPeriod, rules: LaunchRules) -> LaunchPlan:
    return plan_launch(
        key,
        inputs.title,
        period,
        inputs.ads,
        inputs.href,
        inputs.address,
        inputs.counters,
        rules,
        keywords=inputs.keywords,
        region_id=inputs.region_id,
    )


def _marking(account: str, launch_id: int, card: Mapping[str, Any]) -> ProposedChange:
    # Другие данные владельца — другая запись; те же — та же, повтор её не удваивает.
    key = ":".join(str(card[name]) for name in sorted(card))
    return ProposedChange(
        account=account,
        source=ChangeSource.USER,
        kind=ChangeKind.SET_ADVERTISER,
        target_ref=f"{CLIENT_PREFIX}{account}",
        planned=dict(card),
        explanation=(
            f"Передал в Яндекс данные рекламодателя для маркировки, ИНН …{card['inn_tail']}."
            " Почему: без них Яндекс не покажет рекламу. Дальше: Яндекс передаст их в ОРД."
            " Отменить нельзя: исправьте данные в настройках, и я отправлю новые."
        ),
        idempotency_key=f"advertiser:{launch_id}:{key}",
    )


async def _marked(ledger: Ledger, stored: StoredLaunch, card: Mapping[str, Any]) -> bool:
    """Данные рекламодателя на площадке: проверено чтением после записи в журнал."""

    async def applied() -> bool:
        changes = await ledger.changes_on_target(stored.account, f"{CLIENT_PREFIX}{stored.account}")
        return any(
            c.kind is ChangeKind.SET_ADVERTISER
            and c.status is ChangeStatus.APPLIED
            and dict(c.planned) == dict(card)
            for c in changes
        )

    if await applied():
        return True
    await ledger.submit(_marking(stored.account, stored.id, card))
    await ledger.execute_pending(stored.account)
    return await applied()


def _launched(change: Change) -> LaunchedCampaign:
    after = change.after or {}
    campaign = NewCampaign.from_planned(
        change.target_ref.removeprefix(LAUNCH_PREFIX), change.planned
    )
    return LaunchedCampaign(
        campaign_id=str(after["campaign_id"]),
        name=campaign.name,
        limit=campaign.limit,
        href=campaign.href,
        ads=campaign.ads,
        ad_ids=tuple(str(ad_id) for ad_id in after.get("ad_ids", [])),
    )


async def _creations(ledger: Ledger, stored: StoredLaunch) -> list[Change]:
    """Записи создания этого запуска: по одной последней на кампанию."""
    latest: list[Change] = []
    index = 1
    while True:
        target = f"{LAUNCH_PREFIX}ah-{stored.id}-{index}"
        rows = [
            row
            for row in await ledger.changes_on_target(stored.account, target)
            if row.kind is ChangeKind.CREATE_CAMPAIGN
        ]
        if not rows:
            return latest
        latest.append(max(rows, key=lambda row: row.id))
        index += 1


def failure_message(code: str | None) -> str | None:
    """Причина отказа или ожидания словами владельца; код остаётся в журнале."""
    return failure_text(code)


def _result(stored: StoredLaunch, changes: Sequence[Change]) -> LaunchResult:
    ends_at = stored.period.ends_at
    refused = [c for c in changes if c.status in {ChangeStatus.FAILED, ChangeStatus.REJECTED}]
    if refused:
        return LaunchResult(LaunchStatus.REFUSED, reason=refused[0].failure_reason, ends_at=ends_at)
    if not changes or any(c.status is not ChangeStatus.APPLIED for c in changes):
        return LaunchResult(LaunchStatus.PENDING, ends_at=ends_at)
    return LaunchResult(
        LaunchStatus.DONE, created=tuple(_launched(c) for c in changes), ends_at=ends_at
    )


async def expand_keywords_with_model(
    model: TextModel | None,
    services: Sequence[Any],
    niche: str = "",
    city: str = "",
    address: str = "",
    include_autotargeting: bool = True,
    max_per_service: int = 15,
) -> tuple[str, ...]:
    """Формирует расширенное семантическое ядро с привлечением языковой модели.

    Если модель передана, запрашивает у неё разговорные синонимы и сленг для услуг,
    очищает их детерминированным фильтром и объединяет с прямыми запросами.
    При сбое модели или её отсутствии безопасно откатывается к детерминированному правилу.
    """
    if not services:
        return generate_keywords(
            services=services,
            niche=niche,
            city=city,
            address=address,
            include_autotargeting=include_autotargeting,
            max_per_service=max_per_service,
        )

    synonyms: list[str] = []
    if model is not None:
        try:
            service_names = [getattr(s, "name", str(s)) for s in services[:10]]
            services_text = "\n".join(f"- {name}" for name in service_names if name.strip())
            user_prompt = f"Ниша: {niche or 'услуги'}\nУслуги бизнеса:\n{services_text}"
            reply = await model.complete_json(
                KEYWORDS_EXPANSION_SYSTEM,
                user_prompt,
                KEYWORDS_EXPANSION_SCHEMA,
                max_tokens=300,
            )
            synonyms = parse_synonyms_response(reply.text)
        except Exception as error:
            if str(error) == "length":
                logger.warning(
                    "Семантическое расширение ключевых фраз обрезано лимитом модели; "
                    "использую обычные фразы"
                )
            else:
                logger.warning(
                    "Семантическое расширение ключевых фраз через модель не удалось: %s",
                    type(error).__name__,
                )
            synonyms = []

    combined_services: list[Any] = list(services) + synonyms
    return generate_keywords(
        services=combined_services,
        niche=niche,
        city=city,
        address=address,
        include_autotargeting=include_autotargeting,
        max_per_service=max_per_service,
    )


def preview(
    inputs: LaunchInputs, rules: LaunchRules, budget_rules: BudgetRules, now: datetime
) -> LaunchPlan:
    """Что создаст запуск, если нажать сейчас: для сводки готовности, площадку не трогает."""
    starts_at, ends_at = moscow_period(platform_date(now), rules.period_days)
    period = BudgetPeriod(inputs.monthly, budget_rules.reserve_bp, starts_at, ends_at)
    return _plan("0", inputs, period, rules)


async def launch_state(store: LaunchStore, ledger: Ledger) -> LaunchResult | None:
    """Что с запуском сейчас, без обращения к площадке. None — запуска ещё не было."""
    stored = await store.get()
    if stored is None:
        return None
    return _result(stored, await _creations(ledger, stored))


async def launch(
    store: LaunchStore,
    ledger: Ledger,
    inputs: LaunchInputs,
    rules: LaunchRules,
    budget_rules: BudgetRules,
    now: datetime,
    advertiser: Mapping[str, Any] | None = None,
    require_advertiser: bool = False,
) -> LaunchResult:
    """`advertiser` — карточка маркировки без реквизитов (`Advertiser.card`).

    Она уходит на площадку первой: журнал выполняет записи по порядку, и кампании ждут её.
    """
    stored = await store.get()
    if stored is None:
        starts_at, ends_at = moscow_period(platform_date(now), rules.period_days)
        period = BudgetPeriod(inputs.monthly, budget_rules.reserve_bp, starts_at, ends_at)
        check = _plan("0", inputs, period, rules)
        if check.problem is not None:
            return LaunchResult(
                LaunchStatus.PROBLEM, problem=check.problem, minimum_monthly=check.minimum_monthly
            )
        try:
            stored = await store.start(
                inputs.account, period, rules.version, budget_rules.version, now
            )
        except ActivePeriodExists:
            return LaunchResult(LaunchStatus.REFUSED, reason="period_exists")

    # План строим заново от сохранённого периода: повтор после сбоя даёт те же метки и лимиты.
    plan = _plan(str(stored.id), inputs, stored.period, rules)
    # Упавшее создание не считается начатым: повтор после устранения причины ставит его заново.
    started = {
        c.target_ref
        for c in await _creations(ledger, stored)
        if c.status not in {ChangeStatus.FAILED, ChangeStatus.REJECTED}
    }
    if require_advertiser and (advertiser is None or not advertiser.get("inn_tail")):
        raise UnregisteredAdvertiserError(
            "Нельзя создать кампанию: рекламодатель не зарегистрирован (нет ИНН для маркировки)"
        )
    try:
        if advertiser is not None and not await _marked(ledger, stored, advertiser):
            # Без маркировки Яндекс не покажет рекламу: кампании ждут, пока она дойдёт.
            logger.info("Запуск %s ждёт подтверждения маркировки рекламодателя", stored.id)
            return LaunchResult(
                LaunchStatus.PENDING, reason="marking_pending", ends_at=stored.period.ends_at
            )
        for campaign in plan.campaigns:
            if campaign.target_ref in started:
                continue
            await ledger.submit(
                ProposedChange(
                    account=stored.account,
                    source=ChangeSource.USER,
                    kind=ChangeKind.CREATE_CAMPAIGN,
                    target_ref=campaign.target_ref,
                    planned=campaign.planned(),
                    explanation=_explanation(campaign),
                    idempotency_key=f"create:{campaign.marker}",
                )
            )
        await ledger.execute_pending(stored.account)
    except GatewayUnavailable:
        # Записи «planned» остались в журнале: согласование или следующий запуск их завершит.
        logger.warning("Запуск %s приостановлен: Яндекс не отвечает, запись сохранена", stored.id)
        return LaunchResult(
            LaunchStatus.PENDING, reason="gateway_unavailable", ends_at=stored.period.ends_at
        )
    return _result(stored, await _creations(ledger, stored))
