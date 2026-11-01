from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from adhelper.stats.domain.model import (
    AdLine,
    AlertLine,
    CampaignLine,
    Dashboard,
    DayLine,
    HistoryLine,
    PeriodLine,
    Scope,
    Totals,
)

# Сутки статистики Директа считаются по московскому времени.
PLATFORM_ZONE = ZoneInfo("Europe/Moscow")
BP = 10_000
# Длиннее года отчёт не нужен владельцу и тяжёл для базы.
MAX_DAYS = 366
WHOLE = Scope()


class BadPeriod(ValueError):
    """Период задан неверно: начало позже конца, конец в будущем или дольше года."""


@dataclass(frozen=True)
class Window:
    """Дни статистики по московскому времени, обе границы включены."""

    since: date
    until: date

    @property
    def length(self) -> int:
        return (self.until - self.since).days + 1

    def previous(self) -> "Window":
        """Столько же дней сразу перед периодом: с ним сравниваем показатели."""
        until = self.since - timedelta(days=1)
        return Window(until - timedelta(days=self.length - 1), until)


def window(today: date, since: date | None, until: date | None, days: int = 14) -> Window:
    until = until or today
    since = since or until - timedelta(days=days - 1)
    if since > until or until > today or (until - since).days + 1 > MAX_DAYS:
        raise BadPeriod
    return Window(since, until)


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
    until: date
    money: list[MoneyView]
    alerts: list[AlertLine]
    totals: Totals
    ratios: Ratios
    # Те же показатели за столько же дней до периода.
    previous: Totals
    campaigns: list[tuple[CampaignLine, Ratios]]
    days: list[DayLine]


@dataclass(frozen=True)
class CampaignDetail:
    since: date
    until: date
    campaign: CampaignLine
    ratios: Ratios
    previous: Totals
    days: list[DayLine]
    ads: list[tuple[AdLine, Ratios]]


@dataclass(frozen=True)
class AdDetail:
    since: date
    until: date
    ad: AdLine
    campaign_name: str | None
    ratios: Ratios
    previous: Totals
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


def platform_today(now: datetime) -> date:
    return now.astimezone(PLATFORM_ZONE).date()


async def _previous(dashboard: Dashboard, period: Window, scope: Scope) -> Totals:
    earlier = period.previous()
    return _sum([d.totals for d in await dashboard.days(earlier.since, earlier.until, scope)])


async def overview(
    dashboard: Dashboard,
    now: datetime,
    tolerance_bp: Callable[[str], int],
    days: int = 14,
    since: date | None = None,
    until: date | None = None,
) -> Overview:
    today = platform_today(now)
    period = window(today, since, until, days)
    campaigns = await dashboard.campaigns(period.since, period.until)
    totals = _sum([c.totals for c in campaigns])
    return Overview(
        today=today,
        since=period.since,
        until=period.until,
        money=[
            money_view(p, now, tolerance_bp(p.rules_version)) for p in await dashboard.periods(now)
        ],
        alerts=await dashboard.alerts(),
        totals=totals,
        ratios=ratios(totals),
        previous=await _previous(dashboard, period, WHOLE),
        campaigns=[(c, ratios(c.totals)) for c in campaigns],
        days=await dashboard.days(period.since, period.until, WHOLE),
    )


async def ads(dashboard: Dashboard, period: Window) -> list[tuple[AdLine, Ratios]]:
    return [
        (ad, ratios(ad.totals)) for ad in await dashboard.ads(period.since, period.until, WHOLE)
    ]


async def campaign_detail(
    dashboard: Dashboard, period: Window, account: str, campaign_id: str
) -> CampaignDetail | None:
    found = [
        c
        for c in await dashboard.campaigns(period.since, period.until)
        if (c.account, c.campaign_id) == (account, campaign_id)
    ]
    if not found:
        return None
    scope = Scope(account=account, campaign_id=campaign_id)
    lines = await dashboard.ads(period.since, period.until, scope)
    return CampaignDetail(
        since=period.since,
        until=period.until,
        campaign=found[0],
        ratios=ratios(found[0].totals),
        previous=await _previous(dashboard, period, scope),
        days=await dashboard.days(period.since, period.until, scope),
        ads=[(ad, ratios(ad.totals)) for ad in lines],
    )


async def ad_detail(
    dashboard: Dashboard, period: Window, account: str, ad_id: str
) -> AdDetail | None:
    scope = Scope(account=account, ad_id=ad_id)
    found = await dashboard.ads(period.since, period.until, scope)
    if not found:
        return None
    ad = found[0]
    names = {
        c.campaign_id: c.name
        for c in await dashboard.campaigns(period.since, period.until)
        if c.account == account
    }
    return AdDetail(
        since=period.since,
        until=period.until,
        ad=ad,
        campaign_name=names.get(ad.campaign_id),
        ratios=ratios(ad.totals),
        previous=await _previous(dashboard, period, scope),
        days=await dashboard.days(period.since, period.until, scope),
    )


async def history(dashboard: Dashboard, limit: int = 200) -> list[HistoryLine]:
    return await dashboard.history(limit)
