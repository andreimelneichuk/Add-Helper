from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from adhelper.stats.domain.model import (
    AlertLine,
    CampaignLine,
    Dashboard,
    DayLine,
    HistoryLine,
    PeriodLine,
    Totals,
)

# Сутки статистики Директа считаются по московскому времени.
PLATFORM_ZONE = ZoneInfo("Europe/Moscow")
BP = 10_000


@dataclass(frozen=True)
class Ratios:
    """Производные показатели в целых числах: доли в базисных пунктах, цены в микроединицах.
    None — делить не на что, интерфейс так и пишет."""

    ctr_bp: int | None
    cpc: int | None
    cpa: int | None
    conversion_bp: int | None


@dataclass(frozen=True)
class MoneyView:
    period: PeriodLine
    # Сколько можно потратить: жёсткий лимит без запаса на запаздывание статистики.
    spendable: int
    elapsed_bp: int
    # Расход к концу периода при нынешнем темпе. Только для показа, решения его не используют.
    forecast: int | None
    # Сколько ушло со счёта сверх расхода по статистике с точки отсчёта сверки.
    balance_gap: int | None
    # Допуск сверки из правил бюджета: расхождение до него объясняется запаздыванием данных.
    balance_tolerance: int


@dataclass(frozen=True)
class Overview:
    today: date
    since: date
    money: list[MoneyView]
    alerts: list[AlertLine]
    totals: Totals
    ratios: Ratios
    campaigns: list[tuple[CampaignLine, Ratios]]
    days: list[DayLine]


def ratios(totals: Totals) -> Ratios:
    def share(part: int, whole: int) -> int | None:
        return part * BP // whole if whole else None

    return Ratios(
        ctr_bp=share(totals.clicks, totals.impressions),
        cpc=totals.cost // totals.clicks if totals.clicks else None,
        cpa=totals.cost // totals.conversions if totals.conversions else None,
        conversion_bp=share(totals.conversions, totals.clicks),
    )


def money_view(period: PeriodLine, now: datetime, tolerance_bp: int) -> MoneyView:
    reserve = period.hard_limit * period.reserve_bp // BP
    length = (period.ends_at - period.starts_at).total_seconds()
    passed = min(max((now - period.starts_at).total_seconds(), 0), length)
    elapsed_bp = int(passed * BP // length) if length else BP
    forecast = None
    if period.spent is not None and elapsed_bp >= BP // 20:
        forecast = period.spent * BP // elapsed_bp
    gap = None
    if (
        period.opening_balance is not None
        and period.last_balance is not None
        and period.spent is not None
    ):
        left_account = period.opening_balance - period.last_balance
        gap = left_account - (period.spent - (period.opening_spent or 0))
    tolerance = period.hard_limit * tolerance_bp // BP
    return MoneyView(period, period.hard_limit - reserve, elapsed_bp, forecast, gap, tolerance)


def _sum(lines: list[Totals]) -> Totals:
    return Totals(
        sum(t.impressions for t in lines),
        sum(t.clicks for t in lines),
        sum(t.cost for t in lines),
        sum(t.conversions for t in lines),
    )


async def overview(
    dashboard: Dashboard,
    now: datetime,
    tolerance_bp: Callable[[str], int],
    days: int = 14,
) -> Overview:
    today = now.astimezone(PLATFORM_ZONE).date()
    since = today - timedelta(days=days - 1)
    campaigns = await dashboard.campaigns(since, today)
    totals = _sum([c.totals for c in campaigns])
    return Overview(
        today=today,
        since=since,
        money=[
            money_view(p, now, tolerance_bp(p.rules_version)) for p in await dashboard.periods(now)
        ],
        alerts=await dashboard.alerts(),
        totals=totals,
        ratios=ratios(totals),
        campaigns=[(c, ratios(c.totals)) for c in campaigns],
        days=await dashboard.days(since, today),
    )


async def history(dashboard: Dashboard, limit: int = 200) -> list[HistoryLine]:
    return await dashboard.history(limit)
