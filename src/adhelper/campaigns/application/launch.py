"""Запуск рекламы из мастера: период бюджета и кампании, тем же путём, что любое изменение.

План → период бюджета и строка запуска одной транзакцией → журнал со снимком «до» →
исполнитель → чтение результата. Повтор продолжает начатое: созданная кампания находится
по метке и второй раз не создаётся.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from adhelper.budget.domain.calendar import MOSCOW as MOSCOW_TZ
from adhelper.budget.domain.calendar import moscow_period, platform_date
from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.budget.domain.rules import BudgetRules
from adhelper.campaigns.domain.launch import (
    LAUNCH_PREFIX,
    MICROS_PER_RUB,
    PLACEMENT_WORDS,
    LaunchPlan,
    LaunchProblem,
    LaunchRules,
    NewAd,
    NewCampaign,
    plan_launch,
)
from adhelper.campaigns.domain.ports import ActivePeriodExists, LaunchStore, StoredLaunch
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import (
    Change,
    ChangeKind,
    ChangeSource,
    ChangeStatus,
    ProposedChange,
)
from adhelper.ledger.domain.ports import GatewayUnavailable

# Типы домена нужны HTTP-слою, который сам домен не импортирует.
__all__ = [
    "MOSCOW_TZ",
    "PLACEMENT_WORDS",
    "LaunchInputs",
    "LaunchPlan",
    "LaunchProblem",
    "LaunchResult",
    "LaunchStatus",
    "LaunchedCampaign",
    "NewAd",
    "launch",
    "launch_state",
    "preview",
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
    )


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
) -> LaunchResult:
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
    started = {c.target_ref for c in await _creations(ledger, stored)}
    try:
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
        return LaunchResult(LaunchStatus.PENDING, ends_at=stored.period.ends_at)
    return _result(stored, await _creations(ledger, stored))
