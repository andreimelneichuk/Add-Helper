"""Прогон правил по дням: синтетическим для сценариев или из истории кабинета.

`compare` прогоняет две версии правил на одних днях и называет, где решения разошлись.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from adhelper.autopilot.domain.decide import Action, ActionKind, Mode
from adhelper.autopilot.domain.health import HealthInput, HealthReport, Window, check_health
from adhelper.autopilot.domain.model import (
    AdDay,
    AdStats,
    CampaignStats,
    DailyStat,
    Day,
    History,
    mature_totals,
)
from adhelper.autopilot.domain.planner import plan_by_rules
from adhelper.autopilot.domain.rules import AutopilotRules
from adhelper.budget.domain.calendar import MOSCOW

__all__ = ["AdDay", "CycleLog", "Day", "Difference", "History", "compare", "simulate"]


@dataclass(frozen=True)
class CycleLog:
    at: datetime
    health: HealthReport
    protect_only: bool
    actions: Sequence[Action]


@dataclass(frozen=True)
class Difference:
    """Решение по объекту в один день под старыми и под новыми правилами."""

    day: date
    ref: str
    before: str | None
    after: str | None


@dataclass
class _Campaign:
    last_change_at: datetime | None = None
    test_until: date | None = None


@dataclass
class _Ad:
    ref: str
    campaign_id: str
    group_id: str
    first_day: date
    active: bool = True
    rows: list[DailyStat] = field(default_factory=list)


def _window(rows: Sequence[DailyStat]) -> Window:
    visits = [row.visits for row in rows]
    return Window(
        sum(row.spend for row in rows),
        sum(row.leads for row in rows),
        sum(row.clicks for row in rows),
        None if None in visits else sum(v for v in visits if v is not None),
    )


def _mark_untrusted(ads: dict[str, _Ad], day: date) -> None:
    for ad in ads.values():
        ad.rows = [
            DailyStat(r.day, r.spend, r.leads, r.clicks, r.visits, trusted=False)
            if r.day == day
            else r
            for r in ad.rows
        ]


def _campaigns(
    limits: Mapping[str, int] | None, state: dict[str, _Campaign], day: date
) -> list[CampaignStats]:
    if limits is None:
        return []
    result = []
    for cid, limit in limits.items():
        until = state[cid].test_until
        running = until is not None and day < until
        result.append(CampaignStats(cid, limit, False, state[cid].last_change_at, running))
    return result


def simulate(
    days: Sequence[Day],
    target: int,
    rules: AutopilotRules,
    limits: Mapping[str, int] | None = None,
) -> list[CycleLog]:
    """Прогон правил на записанных или синтетических днях, один цикл в конце суток.

    Без лимитов кампаний цикл не знает бюджета, как раньше. С лимитами учитываются
    промежуток после изменения и идущий тест: тест длится `test_days`, исход его не
    моделируется, объявление остаётся как было.
    """
    ads: dict[str, _Ad] = {}
    state = {cid: _Campaign() for cid in limits or {}}
    log: list[CycleLog] = []
    for current in days:
        at = datetime.combine(current.day, time(23, 59), MOSCOW).astimezone(UTC)
        # Объявление без строки в этот день не работало: в истории его выключили вне цикла.
        present = {item.ref for item in current.ads}
        for item in current.ads:
            ad = ads.setdefault(
                item.ref, _Ad(item.ref, item.campaign_id, item.group_id, current.day)
            )
            if ad.active:
                ad.rows.append(
                    DailyStat(current.day, item.spend, item.leads, item.clicks, item.visits)
                )
        boundary = current.day - timedelta(days=rules.health_recent_days)
        all_rows = [row for ad in ads.values() for row in ad.rows]
        recent = _window([row for row in all_rows if row.day > boundary])
        baseline = _window([row for row in all_rows if row.day <= boundary and row.trusted])
        data = HealthInput(recent, baseline, current.platform_incident, False)
        health = check_health(data, rules)
        if not health.trusted:
            _mark_untrusted(ads, current.day)
        stats = []
        for ad in ads.values():
            totals = mature_totals(ad.rows, at, rules)
            age = (current.day - ad.first_day).days
            stats.append(
                AdStats(
                    ad.ref,
                    ad.campaign_id,
                    ad.group_id,
                    totals.spend,
                    totals.leads,
                    age,
                    ad.active and ad.ref in present,
                    clicks=totals.clicks,
                )
            )
        campaigns = _campaigns(limits, state, current.day)
        plan = plan_by_rules(stats, campaigns, target, health, Mode.AUTO, at, rules)
        for action in plan.actions:
            if action.kind is ActionKind.PAUSE_AD:
                ads[action.ref].active = False
            if action.campaign_id in state:
                campaign = state[action.campaign_id]
                campaign.last_change_at = at
                if action.kind is ActionKind.START_TEST:
                    campaign.test_until = current.day + timedelta(days=rules.test_days)
        log.append(CycleLog(at, health, plan.protect_only, plan.actions))
    return log


def _decisions(entry: CycleLog) -> dict[str, str]:
    if entry.protect_only:
        return {"account": "protect_only"}
    return {action.ref: action.kind.value for action in entry.actions}


def compare(
    days: Sequence[Day],
    target: int,
    before: AutopilotRules,
    after: AutopilotRules,
    limits: Mapping[str, int] | None = None,
) -> list[Difference]:
    """Где две версии правил решили по-разному на тех же днях.

    Каждая версия идёт своим путём: после первого расхождения состояния тоже расходятся,
    поэтому сравнение показывает и последствия, а не только первый день.
    """
    differences: list[Difference] = []
    old_log = simulate(days, target, before, limits)
    new_log = simulate(days, target, after, limits)
    for old, new in zip(old_log, new_log, strict=True):
        was, now = _decisions(old), _decisions(new)
        day = old.at.astimezone(MOSCOW).date()
        for ref in sorted(was.keys() | now.keys()):
            if was.get(ref) != now.get(ref):
                differences.append(Difference(day, ref, was.get(ref), now.get(ref)))
    return differences
