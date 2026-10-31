from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from adhelper.autopilot.domain.decide import Action, ActionKind, Mode, plan_cycle
from adhelper.autopilot.domain.health import HealthInput, HealthReport, Window, check_health
from adhelper.autopilot.domain.model import AdStats, DailyStat, mature_totals
from adhelper.autopilot.domain.rules import AutopilotRules
from adhelper.budget.domain.calendar import MOSCOW


@dataclass(frozen=True)
class AdDay:
    ref: str
    campaign_id: str
    group_id: str
    spend: int
    leads: int
    clicks: int
    visits: int


@dataclass(frozen=True)
class Day:
    day: date
    ads: Sequence[AdDay]
    platform_incident: bool = False


@dataclass(frozen=True)
class CycleLog:
    at: datetime
    health: HealthReport
    protect_only: bool
    actions: Sequence[Action]


@dataclass
class _Ad:
    ref: str
    campaign_id: str
    group_id: str
    first_day: date
    active: bool = True
    rows: list[DailyStat] = field(default_factory=list)


def _window(rows: Sequence[DailyStat]) -> Window:
    return Window(
        sum(row.spend for row in rows),
        sum(row.leads for row in rows),
        sum(row.clicks for row in rows),
        sum(row.visits for row in rows),
    )


def _mark_untrusted(ads: dict[str, _Ad], day: date) -> None:
    for ad in ads.values():
        ad.rows = [
            DailyStat(r.day, r.spend, r.leads, r.clicks, r.visits, trusted=False)
            if r.day == day
            else r
            for r in ad.rows
        ]


def simulate(days: Sequence[Day], target: int, rules: AutopilotRules) -> list[CycleLog]:
    """Прогон правил на записанных или синтетических днях, один цикл в конце суток.

    Тесты A/B здесь не моделируются: START_TEST записывается, но ничего не меняет.
    """
    ads: dict[str, _Ad] = {}
    log: list[CycleLog] = []
    for current in days:
        at = datetime.combine(current.day, time(23, 59), MOSCOW).astimezone(UTC)
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
                    ad.ref, ad.campaign_id, ad.group_id, totals.spend, totals.leads, age, ad.active
                )
            )
        plan = plan_cycle(stats, [], target, health, Mode.AUTO, at, rules)
        for action in plan.actions:
            if action.kind is ActionKind.PAUSE_AD:
                ads[action.ref].active = False
        log.append(CycleLog(at, health, plan.protect_only, plan.actions))
    return log
