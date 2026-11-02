"""Просьба владельца сменить район или часы показа: тем же путём, что и любое изменение.

Проверка → журнал со снимком «до» → исполнитель → чтение результата.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from adhelper.campaigns.domain.ports import TARGETING_PREFIX
from adhelper.campaigns.domain.targeting import (
    Schedule,
    TargetingRules,
    canonical_regions,
    check_regions,
    check_schedule,
    describe_schedule,
)
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
    "TARGETING_PREFIX",
    "DisplaySummary",
    "Schedule",
    "TargetingResult",
    "TargetingRules",
    "TargetingStatus",
    "change_regions",
    "change_schedule",
    "describe_regions",
    "describe_schedule",
    "summarize",
    "vitrine_patch",
]
UNDO = "Отменить можно в истории."


class TargetingStatus(StrEnum):
    APPLIED = "applied"
    REFUSED = "refused"
    UNCONFIRMED = "unconfirmed"
    NO_CAMPAIGNS = "no_campaigns"


@dataclass
class TargetingResult:
    status: TargetingStatus
    problem: str | None = None
    applied: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    # Что ставили: по нему витрина узнаёт новое значение без лишнего чтения площадки.
    planned: Mapping[str, Any] = field(default_factory=dict)


def describe_regions(regions: Sequence[int], rules: TargetingRules) -> str:
    return ", ".join(rules.regions.get(region, str(region)) for region in regions)


async def change_schedule(
    ledger: Ledger,
    account: str,
    campaign_ids: Sequence[str],
    schedule: Schedule,
    rules: TargetingRules,
    request_id: str,
) -> TargetingResult:
    problem = check_schedule(schedule, rules)
    if problem is not None:
        return TargetingResult(TargetingStatus.REFUSED, problem.value)
    text = describe_schedule(schedule)
    return await _apply(
        ledger,
        account,
        campaign_ids,
        ChangeKind.SET_SCHEDULE,
        {"schedule": schedule.as_planned()},
        f"Показываю рекламу {text}: вы попросили. {UNDO}",
        f"schedule:{request_id}",
    )


async def change_regions(
    ledger: Ledger,
    account: str,
    campaign_ids: Sequence[str],
    regions: Sequence[int],
    rules: TargetingRules,
    request_id: str,
) -> TargetingResult:
    chosen = canonical_regions(regions)
    problem = check_regions(chosen, rules)
    if problem is not None:
        return TargetingResult(TargetingStatus.REFUSED, problem.value)
    return await _apply(
        ledger,
        account,
        campaign_ids,
        ChangeKind.SET_REGIONS,
        {"regions": list(chosen)},
        f"Показываю рекламу в регионе: {describe_regions(chosen, rules)}. Вы попросили. {UNDO}",
        f"regions:{request_id}",
    )


async def _apply(
    ledger: Ledger,
    account: str,
    campaign_ids: Sequence[str],
    kind: ChangeKind,
    planned: Mapping[str, Any],
    explanation: str,
    key_prefix: str,
) -> TargetingResult:
    if not campaign_ids:
        return TargetingResult(TargetingStatus.NO_CAMPAIGNS)
    result = TargetingResult(TargetingStatus.APPLIED, planned=dict(planned))
    submitted: list[tuple[str, Change]] = []
    for campaign_id in campaign_ids:
        target_ref = f"{TARGETING_PREFIX}{campaign_id}"
        key = f"{key_prefix}:{campaign_id}"
        # Индекс журнала держит ключ только у незавершённых записей, поэтому готовую ищем сами.
        rows = await ledger.changes_on_target(account, target_ref)
        if any(r.idempotency_key == key and r.status is ChangeStatus.APPLIED for r in rows):
            result.applied.append(campaign_id)
            continue
        try:
            change = await ledger.submit(
                ProposedChange(
                    account=account,
                    source=ChangeSource.USER,
                    kind=kind,
                    target_ref=target_ref,
                    planned=dict(planned),
                    explanation=explanation,
                    idempotency_key=key,
                )
            )
        except GatewayUnavailable:
            result.failed.append(campaign_id)
            continue
        submitted.append((campaign_id, change))
    # Все записи стоят в очереди кабинета; исполнитель проходит их по порядку и читает итог.
    await ledger.execute_pending(account)
    for campaign_id, change in submitted:
        if await ledger.status_of(change.id) is ChangeStatus.APPLIED:
            result.applied.append(campaign_id)
        else:
            result.failed.append(campaign_id)
    order = {campaign_id: index for index, campaign_id in enumerate(campaign_ids)}
    result.applied.sort(key=order.__getitem__)
    result.failed.sort(key=order.__getitem__)
    if result.failed:
        result.status = TargetingStatus.UNCONFIRMED
    return result


SCHEDULE_PLAN = "schedule_plan"
REGION_IDS = "region_ids"


def vitrine_patch(planned: Mapping[str, Any], rules: TargetingRules) -> dict[str, Any]:
    """Что записать в витрину после применения: точные значения и слова для экрана."""
    patch: dict[str, Any] = {}
    if "schedule" in planned:
        schedule = Schedule.from_planned(planned["schedule"])
        patch |= {"schedule": describe_schedule(schedule), SCHEDULE_PLAN: schedule.as_planned()}
    if "regions" in planned:
        regions = canonical_regions(planned["regions"])
        names = [rules.regions.get(region, str(region)) for region in regions]
        patch |= {"regions": names, REGION_IDS: list(regions)}
    return patch


@dataclass(frozen=True)
class DisplaySummary:
    """Общие для всех кампаний район и часы; None, если они разные или неизвестны."""

    schedule: Schedule | None
    regions: tuple[int, ...] | None
    campaigns: int


def summarize(targetings: Sequence[Mapping[str, Any]]) -> DisplaySummary:
    schedules = {
        (tuple(t[SCHEDULE_PLAN]["days"]), tuple(t[SCHEDULE_PLAN]["hours"]))
        if SCHEDULE_PLAN in t
        else None
        for t in targetings
    }
    regions = {tuple(t[REGION_IDS]) if REGION_IDS in t else None for t in targetings}
    schedule = None
    if len(schedules) == 1 and (only := next(iter(schedules))) is not None:
        schedule = Schedule.of(only[0], only[1][0], only[1][1])
    region = next(iter(regions)) if len(regions) == 1 else None
    return DisplaySummary(schedule, region, len(targetings))
