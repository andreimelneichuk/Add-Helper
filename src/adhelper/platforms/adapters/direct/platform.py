"""Директ как площадка: реализует порты нашими понятиями поверх `DirectHttpClient`.

Чтение возвращает ровно те поля, что журнал записал в план, иначе он увидит конфликт.
Создание не идемпотентно, поэтому каждое создание сначала ищет уже созданное по нашей метке
и доделывает недостающее. Где поле Директа не проверено вызовом, это сказано рядом.
"""

import base64
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from adhelper.budget.domain.limits import CampaignBudget, LimitChange, LimitDecision
from adhelper.budget.domain.ports import CampaignStrategy, PlatformUnavailable
from adhelper.budget.domain.watch import SpendSnapshot
from adhelper.campaigns.domain.launch import LAUNCH_PREFIX, NewAd, NewCampaign, Placement
from adhelper.campaigns.domain.ports import CreatedCampaign, Targeting
from adhelper.campaigns.domain.targeting import Schedule
from adhelper.platforms.adapters.direct.geo_regions import GeoRegionChoice, resolve_city_choices
from adhelper.platforms.adapters.direct.transport import (
    DirectError,
    DirectGeoRegionAmbiguous,
    DirectHttpClient,
    DirectRejected,
    TokenSource,
    check_results,
    rubles_to_micros,
)
from adhelper.platforms.domain.ports import (
    AdDay,
    AdImage,
    AdInventory,
    Advertiser,
    CampaignDay,
    CampaignInventory,
    ModerationState,
    ModerationVerdict,
    PublishedAd,
)

MOSCOW = ZoneInfo("Europe/Moscow")
# Кампания без лимита на период (создана не нами или с недельным бюджетом) для стража
# безлимитна: тогда худший случай заведомо больше бюджета и увеличения он не пропустит.
NO_LIMIT = 10**15
# Отчёты Директа отстают от расхода; насколько, не измерено (S3). Берём с запасом: чем старше
# момент данных, тем больше страж добавляет расхода по скорости, это осторожнее.
REPORT_LAG = timedelta(hours=3)
# Где на площадке живёт метка нового объявления: параметр ссылки, при чтении снимаем.
MARKER_PARAM = "ah_ad"
# Включённые кампании: OFF — например, ждёт денег или модерации и может начать тратить.
ACTIVE_CAMPAIGN_STATES = frozenset({"ON", "OFF"})
STOPPED_AD_STATES = frozenset({"SUSPENDED", "ARCHIVED"})
SIDES = ("Search", "Network")
SERVING_OFF = "SERVING_OFF"
# Сети по стратегии поиска: бюджет один, он у поиска.
NETWORK_DEFAULT = "NETWORK_DEFAULT"
# Директ берёт до 10 кампаний в отборе объявлений и групп.
CAMPAIGN_CHUNK = 10
CAMPAIGN_TYPE_KEYS = {"UNIFIED_CAMPAIGN": "UnifiedCampaign", "TEXT_CAMPAIGN": "TextCampaign"}
MODERATION_STATES = {
    "ACCEPTED": ModerationState.ACCEPTED,
    "REJECTED": ModerationState.REJECTED,
}


class GeoRegionCache(Protocol):
    async def get(self) -> list[dict[str, Any]] | None: ...

    async def put(self, regions: Sequence[Mapping[str, Any]]) -> None: ...


logger = logging.getLogger(__name__)
# Директ допускает не более трёх комбинаторных объявлений в группе.
MAX_COMBINATORIAL_ADS_PER_GROUP = 3
MAX_KEYWORDS_PER_GROUP = 200


def _campaign_view_state(campaign: Mapping[str, Any]) -> str:
    state = str(campaign.get("State") or "")
    if state == "ARCHIVED":
        return "archived"
    if campaign.get("Status") == "MODERATION":
        return "moderation"
    return "on" if state == "ON" else "suspended"


def _ad_view_state(ad: Mapping[str, Any]) -> str:
    state = str(ad.get("State") or "")
    status = str(ad.get("Status") or "")
    if state == "ARCHIVED":
        return "archived"
    if state == "SUSPENDED":
        return "suspended"
    if status == "REJECTED":
        return "rejected"
    if status == "MODERATION":
        return "moderation"
    return "on" if state == "ON" else "suspended"


def _today(now: datetime) -> date:
    return now.astimezone(MOSCOW).date()


def _chunks(items: Sequence[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def split_limit(total: int, parts: int) -> list[int]:
    """Лимит на несколько стратегий кампании. Сумма частей ровно равна лимиту.

    Общий ли бюджет у поиска и сетей ЕПК или у каждого свой, не проверено вызовом (S2):
    при общем кампания недотратит, при раздельном не превысит. Чтение складывает части.
    """
    if parts <= 0:
        raise ValueError("Нет стратегий с бюджетом")
    share = total // parts
    return [total - share * (parts - 1)] + [share] * (parts - 1)


def budgets(strategy: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    """Где в стратегии лежат лимиты на период: (сторона, объект стратегии, CustomPeriodBudget)."""
    found: list[tuple[str, str, Mapping[str, Any]]] = []
    for side in SIDES:
        params = strategy.get(side) or {}
        for key, value in params.items():
            if isinstance(value, Mapping) and isinstance(value.get("CustomPeriodBudget"), Mapping):
                found.append((side, key, value["CustomPeriodBudget"]))
    return found


def period_limit(strategy: Mapping[str, Any]) -> int:
    found = budgets(strategy)
    if not found:
        return NO_LIMIT
    return sum(int(budget["SpendLimit"]) for _, _, budget in found)


# Ручное управление ставками: площадка не распределяет расход внутри лимита сама.
MANUAL_STRATEGIES = frozenset({"HIGHEST_POSITION", "MAXIMUM_COVERAGE"})
# Стратегии по кликам: учиться на конверсиях им не на чем.
CLICK_STRATEGIES = frozenset({"WB_MAXIMUM_CLICKS", "AVERAGE_CPC", "WEEKLY_CLICK_PACKAGE"})
# Стратегии по конверсиям учатся, и их обучение через API не видно.
CONVERSION_STRATEGIES = frozenset(
    {
        "WB_MAXIMUM_CONVERSION_RATE",
        "AVERAGE_CPA",
        "AVERAGE_ROI",
        "AVERAGE_CRR",
        "PAY_FOR_CONVERSION",
        "PAY_FOR_CONVERSION_CRR",
        "AVERAGE_CPA_MULTIPLE_GOALS",
        "PAY_FOR_CONVERSION_MULTIPLE_GOALS",
        "MAX_PROFIT",
    }
)


def strategy_of(strategy: Mapping[str, Any], start: str | None) -> CampaignStrategy:
    """Автостратегия и обучение по `BiddingStrategyType` обеих сторон.

    Обучение стратегии по конверсиям для нас «не знаем» (None), и политика денег не трогает.
    Незнакомый тип — тоже «не знаем» и по автостратегии, и по обучению.
    """
    types = [str((strategy.get(side) or {}).get("BiddingStrategyType") or "") for side in SIDES]
    active = [kind for kind in types if kind and kind not in (SERVING_OFF, NETWORK_DEFAULT)]
    known = MANUAL_STRATEGIES | CLICK_STRATEGIES | CONVERSION_STRATEGIES
    auto: bool | None = None
    learns: bool | None = None
    if active and all(kind in known for kind in active):
        auto = any(kind not in MANUAL_STRATEGIES for kind in active)
        if not any(kind in CONVERSION_STRATEGIES for kind in active):
            learns = False
    try:
        started = date.fromisoformat(start) if start else None
    except ValueError:
        started = None
    return CampaignStrategy(auto=auto, learns=learns, started=started)


def placement_of(strategy: Mapping[str, Any]) -> Placement:
    on = [
        side
        for side in SIDES
        if (strategy.get(side) or {}).get("BiddingStrategyType") not in (None, SERVING_OFF)
    ]
    if on == ["Search"]:
        return Placement.SEARCH
    if on == ["Network"]:
        return Placement.NETWORKS
    return Placement.ALL


def schedule_items(schedule: Schedule) -> list[str]:
    """`TimeTargeting.Schedule.Items`: день 1–7, затем 24 коэффициента, 0 выключает час."""
    items = []
    for day in range(7):
        hours = [
            100 if day in schedule.days and schedule.start <= hour < schedule.end else 0
            for hour in range(24)
        ]
        items.append(",".join(str(value) for value in [day + 1, *hours]))
    return items


# Расписание, которое нельзя выразить нашими понятиями: с ним никакой план не совпадёт,
# и журнал увидит конфликт, а не ложное «уже сделано».
UNREPRESENTABLE = Schedule((), 0, 0)
ALWAYS = Schedule((0, 1, 2, 3, 4, 5, 6), 0, 24)


def parse_schedule(targeting: Mapping[str, Any] | None) -> Schedule:
    items = ((targeting or {}).get("Schedule") or {}).get("Items") or []
    if not items:
        return ALWAYS
    windows: dict[int, tuple[int, int]] = {}
    for item in items:
        values = [int(part) for part in str(item).split(",")]
        if len(values) != 25:
            return UNREPRESENTABLE
        day, hours = values[0] - 1, values[1:]
        if any(value not in (0, 100) for value in hours):
            return UNREPRESENTABLE
        on = [hour for hour, value in enumerate(hours) if value]
        if not on:
            continue
        if on != list(range(on[0], on[-1] + 1)):
            return UNREPRESENTABLE
        windows[day] = (on[0], on[-1] + 1)
    if not windows or len(set(windows.values())) != 1:
        return UNREPRESENTABLE
    start, end = next(iter(windows.values()))
    return Schedule.of(windows, start, end)


def mark_href(href: str, marker: str) -> str:
    base, hash_, fragment = href.partition("#")
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{MARKER_PARAM}={marker}{hash_}{fragment}"


def unmark_href(href: str) -> tuple[str, str | None]:
    """Ссылка без нашей метки и сама метка. Обратно к `mark_href`."""
    base, hash_, fragment = href.partition("#")
    for separator in ("&", "?"):
        suffix = f"{separator}{MARKER_PARAM}="
        index = base.rfind(suffix)
        if index >= 0 and "&" not in base[index + len(suffix) :]:
            return base[:index] + hash_ + fragment, base[index + len(suffix) :]
    return href, None


def ends_at_of(end_date: str) -> datetime:
    """Последний день бюджета в Директе → конец периода: следующая московская полночь в UTC."""
    last = date.fromisoformat(end_date)
    return datetime.combine(last + timedelta(days=1), time.min, tzinfo=MOSCOW).astimezone(UTC)


def last_day_of(ends_at: datetime) -> date:
    return ends_at.astimezone(MOSCOW).date() - timedelta(days=1)


def parse_report(tsv: str) -> list[dict[str, str]]:
    """TSV без строки названия и итога: первая строка — названия столбцов."""
    lines = [line for line in tsv.splitlines() if line.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"), strict=True)) for line in lines[1:]]


def _cost(raw: str) -> int:
    # Пусто или «--» — расхода нет. Деньги в микроединицах: returnMoneyInMicros.
    return int(raw) if raw.strip().isdigit() else 0


def _conversions(row: Mapping[str, str]) -> int:
    """Обращения за строку отчёта: без отбора целей один столбец, с отбором по столбцу на цель."""
    return sum(_count(value) for name, value in row.items() if name.startswith("Conversions"))


def _count(raw: str) -> int:
    # «--» — значения нет: конверсий не было или Метрика не связана.
    return int(raw) if raw.strip().isdigit() else 0


class DirectPlatform:
    """Площадка Директа для одного бизнеса: токены кабинетов берутся из его хранилища."""

    def __init__(
        self,
        client: DirectHttpClient,
        token_for: TokenSource,
        clock: Callable[[], datetime],
        campaign_ids_for: Callable[[str], Awaitable[Sequence[str]]] | None = None,
        campaign_targets_for: Callable[[str], Awaitable[Sequence[str]]] | None = None,
        geo_region_cache: GeoRegionCache | None = None,
        goals_for: Callable[[str], Awaitable[Sequence[int]]] | None = None,
    ) -> None:
        self._goals_for = goals_for
        self._client = client
        self._token_for = token_for
        self._clock = clock
        self._campaign_ids_for = campaign_ids_for
        self._campaign_targets_for = campaign_targets_for
        self._geo_region_cache = geo_region_cache

    async def _managed_campaigns(self, account: str) -> list[Mapping[str, Any]]:
        if self._campaign_ids_for is None:
            return await self._campaigns(account)
        ids = await self._campaign_ids_for(account)
        if not ids:
            return []
        return await self._campaigns(account, [int(campaign_id) for campaign_id in ids])

    async def _call(
        self, account: str, service: str, method: str, params: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        token = await self._token_for(account)
        return await self._client.call(account, token, service, method, params)

    async def _goal_filter(self, account: str) -> dict[str, Any]:
        goals = await self._lead_goals(account)
        return {"Goals": goals} if goals else {}

    async def _lead_goals(self, account: str) -> list[int]:
        """Цели-обращения счётчика; пусто — счётчик или цели неизвестны (тогда все цели)."""
        if self._goals_for is None:
            return []
        try:
            return list(await self._goals_for(account))
        except PlatformUnavailable as error:
            # Без списка целей служебную цель от обращения не отличить. Тихо взять все цели
            # значило бы исказить число и цену обращения, поэтому отчёт не сохраняем.
            raise DirectError(None, "metrika goals unavailable") from error

    async def _report(self, account: str, definition: dict[str, Any]) -> list[dict[str, str]]:
        # Директ кэширует отчёт по имени: одно имя — одно определение.
        digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
        definition = {**definition, "ReportName": f"ah-{digest[:24]}"}
        token = await self._token_for(account)
        return parse_report(await self._client.report(account, token, definition))

    async def geo_region_tree(self, account: str) -> list[dict[str, Any]]:
        """Read the shared cache or fetch the complete official Direct dictionary."""
        cached = await self._geo_region_cache.get() if self._geo_region_cache is not None else None
        if cached is not None:
            return cached
        result = await self._call(
            account, "dictionaries", "get", {"DictionaryNames": ["GeoRegions"]}
        )
        regions = result.get("GeoRegions")
        required = {"GeoRegionId", "GeoRegionName", "GeoRegionType", "ParentId"}
        if (
            not isinstance(regions, list)
            or not regions
            or any(not isinstance(row, Mapping) or not required.issubset(row) for row in regions)
        ):
            raise DirectError(None, "Direct GeoRegions dictionary is empty or malformed")
        normalized = [dict(row) for row in regions]
        if self._geo_region_cache is not None:
            await self._geo_region_cache.put(normalized)
        return normalized

    async def resolve_city_region_choices(
        self, account: str, address: str
    ) -> tuple[GeoRegionChoice, ...]:
        return resolve_city_choices(address, await self.geo_region_tree(account))

    async def resolve_city_region(self, account: str, address: str) -> tuple[int, str] | None:
        """Return a city only when exact matching and address context leave one choice."""
        choices = await self.resolve_city_region_choices(account, address)
        if len(choices) > 1:
            raise DirectGeoRegionAmbiguous(None, "ambiguous Russian city")
        if not choices:
            return None
        return choices[0].region_id, choices[0].display

    # --- Кампании ---

    async def _campaigns(
        self, account: str, ids: Sequence[int] | None = None, extra: Sequence[str] = ()
    ) -> list[Mapping[str, Any]]:
        criteria: dict[str, Any] = {"Ids": list(ids)} if ids else {}
        result = await self._call(
            account,
            "campaigns",
            "get",
            {
                "SelectionCriteria": criteria,
                "FieldNames": ["Id", "Name", "Type", "State", "Status", *extra],
                "UnifiedCampaignFieldNames": ["BiddingStrategy", "CounterIds"],
                "TextCampaignFieldNames": ["BiddingStrategy", "CounterIds"],
            },
        )
        if "LimitedBy" in result:
            # Не все кампании видны: сумма лимитов неизвестна, решать по части нельзя.
            raise DirectError(None, "campaigns list is truncated")
        return list(result.get("Campaigns") or [])

    @staticmethod
    def _strategy(campaign: Mapping[str, Any]) -> Mapping[str, Any]:
        key = CAMPAIGN_TYPE_KEYS.get(str(campaign.get("Type")))
        if key is None:
            return {}
        return (campaign.get(key) or {}).get("BiddingStrategy") or {}

    async def _spent(self, account: str, campaigns: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        """Расход каждой кампании за её бюджетный период, по отчёту, а не по расчёту."""
        starts: dict[str, date] = {}
        today = _today(self._clock())
        for campaign in campaigns:
            found = budgets(self._strategy(campaign))
            start = min(
                (
                    date.fromisoformat(str(b["StartDate"]))
                    for _, _, b in found
                    if b.get("StartDate")
                ),
                default=today,
            )
            starts[str(campaign["Id"])] = min(start, today)
        if not starts:
            return {}
        rows = await self._report(
            account,
            {
                "SelectionCriteria": {
                    "DateFrom": min(starts.values()).isoformat(),
                    "DateTo": today.isoformat(),
                },
                "FieldNames": ["Date", "CampaignId", "Cost"],
                "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
                "DateRangeType": "CUSTOM_DATE",
                "Format": "TSV",
                # Параметры как в кабинете — сверка в S3.
                "IncludeVAT": "YES",
                "IncludeDiscount": "NO",
            },
        )
        spent = dict.fromkeys(starts, 0)
        for row in rows:
            campaign_id = row["CampaignId"]
            if campaign_id in starts and date.fromisoformat(row["Date"]) >= starts[campaign_id]:
                spent[campaign_id] += _cost(row["Cost"])
        return spent

    async def read_campaigns(self, account: str) -> list[CampaignBudget]:
        campaigns = await self._managed_campaigns(account)
        spent = await self._spent(account, campaigns)
        return [
            CampaignBudget(
                campaign_id=str(campaign["Id"]),
                limit=period_limit(self._strategy(campaign)),
                spent=spent.get(str(campaign["Id"]), 0),
                active=campaign.get("State") in ACTIVE_CAMPAIGN_STATES,
                # Когда лимит последний раз повышали, Директ не хранит; ведёт журнал.
                last_increase_at=None,
            )
            for campaign in campaigns
        ]

    async def campaign_strategies(self, account: str) -> dict[str, CampaignStrategy]:
        """Стратегия и день старта управляемых кампаний для политики автопилота."""
        if self._campaign_ids_for is None:
            campaigns = await self._campaigns(account, extra=["StartDate"])
        else:
            ids = await self._campaign_ids_for(account)
            if not ids:
                return {}
            campaigns = await self._campaigns(account, [int(i) for i in ids], extra=["StartDate"])
        return {str(c["Id"]): strategy_of(self._strategy(c), c.get("StartDate")) for c in campaigns}

    async def read_account_campaigns(self, account: str) -> list[CampaignBudget]:
        """Все кампании аккаунта для проверки общего лимита, включая созданные вне сервиса."""
        # Архивные не тратят деньги и не занимают место в бюджете кабинета.
        campaigns = [c for c in await self._campaigns(account) if c.get("State") != "ARCHIVED"]
        spent = await self._spent(account, campaigns)
        return [
            CampaignBudget(
                campaign_id=str(campaign["Id"]),
                limit=period_limit(self._strategy(campaign)),
                spent=spent.get(str(campaign["Id"]), 0),
                active=campaign.get("State") in ACTIVE_CAMPAIGN_STATES,
                last_increase_at=None,
            )
            for campaign in campaigns
        ]

    async def existing_campaign_id(self, account: str, target_ref: str) -> str | None:
        """Return the Direct id when a retried launch already created its marked campaign."""
        if not target_ref.startswith(LAUNCH_PREFIX):
            return None
        campaign = await self._launch_campaign(account, target_ref.removeprefix(LAUNCH_PREFIX))
        return str(campaign["Id"]) if campaign is not None else None

    async def managed_campaign_ids(self, account: str) -> tuple[str, ...]:
        """Campaign IDs owned by this business according to its applied launch journal."""
        if self._campaign_ids_for is None:
            return tuple(str(c["Id"]) for c in await self._campaigns(account))
        campaign_ids = {str(campaign_id) for campaign_id in await self._campaign_ids_for(account)}
        if self._campaign_targets_for is not None:
            targets = await self._campaign_targets_for(account)
            markers = [
                target.removeprefix(LAUNCH_PREFIX)
                for target in targets
                if target.startswith(LAUNCH_PREFIX)
            ]
            if markers:
                campaigns = await self._campaigns(account)
                for campaign in campaigns:
                    if campaign.get("State") == "ARCHIVED":
                        continue
                    name = str(campaign.get("Name", ""))
                    if any(f"({marker})" in name for marker in markers):
                        campaign_ids.add(str(campaign["Id"]))
        return tuple(sorted(campaign_ids))

    async def fetch_spend(self, account: str) -> SpendSnapshot:
        if self._campaign_ids_for is None:
            return await self.fetch_account_spend(account)
        campaigns = await self._managed_campaigns(account)
        campaign_ids = [str(campaign["Id"]) for campaign in campaigns]
        if not campaign_ids:
            return SpendSnapshot(reported=0, data_as_of=self._clock() - REPORT_LAG)
        rows = await self._report(
            account,
            {
                "SelectionCriteria": {
                    "Filter": [
                        {
                            "Field": "CampaignId",
                            "Operator": "IN",
                            "Values": campaign_ids,
                        }
                    ]
                },
                "FieldNames": ["CampaignId", "Cost"],
                "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
                "DateRangeType": "ALL_TIME",
                "Format": "TSV",
                "IncludeVAT": "YES",
                "IncludeDiscount": "NO",
            },
        )
        reported = sum(_cost(row["Cost"]) for row in rows)
        data_as_of = self._clock() - REPORT_LAG
        logger.info(
            "fetch_spend: account=%s, campaigns=%d, reported_micros=%d, data_as_of=%s",
            account,
            len(campaign_ids),
            reported,
            data_as_of.isoformat(),
        )
        return SpendSnapshot(reported=reported, data_as_of=data_as_of)

    async def fetch_account_spend(self, account: str) -> SpendSnapshot:
        """Расход всего аккаунта для сверки его общего баланса."""
        now = self._clock()
        rows = await self._report(
            account,
            {
                "SelectionCriteria": {},
                "FieldNames": ["Cost"],
                "ReportType": "ACCOUNT_PERFORMANCE_REPORT",
                "DateRangeType": "ALL_TIME",
                "Format": "TSV",
                "IncludeVAT": "YES",
                "IncludeDiscount": "NO",
            },
        )
        reported = sum(_cost(row["Cost"]) for row in rows)
        data_as_of = now - REPORT_LAG
        logger.info(
            "fetch_spend: account=%s, reported_micros=%d, data_as_of=%s, report_lag=%s",
            account,
            reported,
            data_as_of.isoformat(),
            REPORT_LAG,
        )
        return SpendSnapshot(reported=reported, data_as_of=data_as_of)

    async def ad_days(
        self, account: str, campaign_ids: Sequence[str], since: date, until: date
    ) -> list[AdDay]:
        """Сутки по объявлениям из `Reports`. `Conversions` без `Goals` — все цели счётчика:
        для сравнения вариантов одной группы набор целей у обоих один и тот же."""
        if not campaign_ids:
            return []
        rows = await self._report(
            account,
            {
                "SelectionCriteria": {
                    "DateFrom": since.isoformat(),
                    "DateTo": until.isoformat(),
                    "Filter": [
                        {
                            "Field": "CampaignId",
                            "Operator": "IN",
                            "Values": [str(c) for c in campaign_ids],
                        }
                    ],
                },
                "FieldNames": ["Date", "AdId", "Impressions", "Clicks", "Cost", "Conversions"],
                **await self._goal_filter(account),
                "ReportType": "AD_PERFORMANCE_REPORT",
                "DateRangeType": "CUSTOM_DATE",
                "Format": "TSV",
                # Как у расхода кампаний: сверка с кабинетом в S3.
                "IncludeVAT": "YES",
                "IncludeDiscount": "NO",
            },
        )
        return [
            AdDay(
                ad_id=row["AdId"],
                day=date.fromisoformat(row["Date"]),
                impressions=_count(row["Impressions"]),
                clicks=_count(row["Clicks"]),
                cost=_cost(row["Cost"]),
                conversions=_conversions(row),
            )
            for row in rows
        ]

    async def campaign_days(
        self, account: str, campaign_ids: Sequence[str], since: date, until: date
    ) -> list[CampaignDay]:
        """Суточные метрики сервисных кампаний из `CAMPAIGN_PERFORMANCE_REPORT`."""
        if not campaign_ids:
            return []
        rows = await self._report(
            account,
            {
                "SelectionCriteria": {
                    "DateFrom": since.isoformat(),
                    "DateTo": until.isoformat(),
                    "Filter": [
                        {
                            "Field": "CampaignId",
                            "Operator": "IN",
                            "Values": [str(c) for c in campaign_ids],
                        }
                    ],
                },
                "FieldNames": [
                    "Date",
                    "CampaignId",
                    "Impressions",
                    "Clicks",
                    "Cost",
                    "Conversions",
                ],
                **await self._goal_filter(account),
                "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
                "DateRangeType": "CUSTOM_DATE",
                "Format": "TSV",
                "IncludeVAT": "YES",
                "IncludeDiscount": "NO",
            },
        )
        return [
            CampaignDay(
                campaign_id=row["CampaignId"],
                day=date.fromisoformat(row["Date"]),
                impressions=_count(row["Impressions"]),
                clicks=_count(row["Clicks"]),
                cost=_cost(row["Cost"]),
                conversions=_conversions(row),
            )
            for row in rows
        ]

    async def fetch_balance(self, account: str) -> int:
        token = await self._token_for(account)
        data = await self._client.live(
            account,
            token,
            "AccountManagement",
            {"Action": "Get", "SelectionCriteria": {"Logins": [account]}},
        )
        accounts = (data or {}).get("Accounts") or []
        mine = [item for item in accounts if str(item.get("Login", "")).lower() == account.lower()]
        if not mine:
            raise DirectError(None, "shared account is not enabled")
        info = mine[0]
        blocked_flag = str(info.get("StatusBlocked", "")).strip().lower() in ("yes", "true")
        blocked_status = str(info.get("Status", "")).strip().lower() == "blocked"
        status_blocked = blocked_flag or blocked_status
        raw_amount = info.get("Amount")
        amount_str = str(raw_amount).strip() if raw_amount is not None else ""
        if status_blocked or not amount_str:
            logger.info("fetch_balance: account=%s заблокирован или остаток отсутствует", account)
            return 0
        micros = rubles_to_micros(amount_str)
        balance = max(0, micros)
        logger.debug("fetch_balance: account=%s, balance_micros=%d", account, balance)
        return balance

    async def set_limit(self, account: str, change: LimitChange) -> None:
        found = await self._campaigns(account, [int(change.campaign_id)])
        if not found:
            raise DirectRejected(None, f"no campaign {change.campaign_id}")
        campaign = found[0]
        strategy = self._strategy(campaign)
        places = budgets(strategy)
        if not places:
            # Кампанию без лимита на период не переводим молча: это меняет её стратегию.
            raise DirectRejected(None, f"campaign {change.campaign_id} has no period budget")
        sides: dict[str, dict[str, Any]] = {}
        for (side, key, budget), part in zip(
            places, split_limit(change.new_limit, len(places)), strict=True
        ):
            entry = sides.setdefault(
                side, {"BiddingStrategyType": strategy[side]["BiddingStrategyType"]}
            )
            entry[key] = {"CustomPeriodBudget": {**budget, "SpendLimit": part}}
        type_key = CAMPAIGN_TYPE_KEYS[str(campaign["Type"])]
        result = await self._call(
            account,
            "campaigns",
            "update",
            {"Campaigns": [{"Id": int(change.campaign_id), type_key: {"BiddingStrategy": sides}}]},
        )
        check_results(result.get("UpdateResults") or [])

    async def _campaign_action(self, account: str, method: str, campaign_ids: list[str]) -> None:
        if not campaign_ids:
            return
        result = await self._call(
            account,
            "campaigns",
            method,
            {"SelectionCriteria": {"Ids": [int(c) for c in campaign_ids]}},
        )
        key = (
            "SuspendResults"
            if method == "suspend"
            else "ArchiveResults"
            if method == "archive"
            else "ResumeResults"
        )
        check_results(result.get(key) or [])

    async def pause_campaigns(self, account: str, campaign_ids: list[str]) -> None:
        await self._campaign_action(account, "suspend", campaign_ids)

    async def resume_campaigns(self, account: str, campaign_ids: list[str]) -> None:
        await self._campaign_action(account, "resume", campaign_ids)

    async def archive_campaigns(self, account: str, campaign_ids: list[str]) -> None:
        await self._campaign_action(account, "archive", campaign_ids)

    async def record_rejection(
        self, account: str, change: LimitChange, decision: LimitDecision
    ) -> None:
        # Отказ стража остаётся у нас в журнале; на площадку ничего не уходит.
        return None

    # --- Объявления ---

    async def _ads(self, account: str, criteria: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        result = await self._call(
            account,
            "ads",
            "get",
            {
                "SelectionCriteria": criteria,
                "FieldNames": [
                    "Id",
                    "CampaignId",
                    "AdGroupId",
                    "State",
                    "Status",
                    "StatusClarification",
                ],
                "TextAdFieldNames": ["Title", "Text", "Href", "AdImageHash"],
            },
        )
        return list(result.get("Ads") or [])

    async def _ads_of_campaigns(
        self, account: str, campaign_ids: Sequence[int]
    ) -> list[Mapping[str, Any]]:
        ads: list[Mapping[str, Any]] = []
        for chunk in _chunks(campaign_ids, CAMPAIGN_CHUNK):
            ads.extend(await self._ads(account, {"CampaignIds": chunk}))
        return sorted(ads, key=lambda ad: int(ad["Id"]))

    async def inventory(
        self, account: str, campaign_ids: Sequence[str]
    ) -> tuple[list[CampaignInventory], list[AdInventory]]:
        """Read managed campaign and ad names and states for the owner's dashboard."""
        ids = [int(campaign_id) for campaign_id in campaign_ids]
        if not ids:
            return [], []
        campaigns = await self._campaigns(account, ids)
        ads = await self._ads_of_campaigns(account, ids)
        campaign_rows = [
            CampaignInventory(
                campaign_id=str(campaign["Id"]),
                name=str(campaign.get("Name") or ""),
                state=_campaign_view_state(campaign),
            )
            for campaign in campaigns
        ]
        ad_rows = []
        for ad in ads:
            text_ad = ad.get("TextAd") or {}
            ad_rows.append(
                AdInventory(
                    ad_id=str(ad["Id"]),
                    campaign_id=str(ad.get("CampaignId") or ""),
                    title=str(text_ad.get("Title") or ""),
                    text=str(text_ad.get("Text") or ""),
                    href=str(text_ad.get("Href") or ""),
                    state=_ad_view_state(ad),
                    group_id=str(ad.get("AdGroupId") or ""),
                )
            )
        return campaign_rows, ad_rows

    async def read_ad(self, account: str, ad_ref: str) -> Mapping[str, Any]:
        ad_id = int(ad_ref.removeprefix("ad:"))
        found = await self._ads(account, {"Ids": [ad_id]})
        if not found:
            return {}
        state = found[0].get("State")
        return {"active": state not in STOPPED_AD_STATES, "archived": state == "ARCHIVED"}

    async def set_ad_active(self, account: str, ad_ref: str, active: bool) -> None:
        ad_id = int(ad_ref.removeprefix("ad:"))
        method = "resume" if active else "suspend"
        result = await self._call(account, "ads", method, {"SelectionCriteria": {"Ids": [ad_id]}})
        check_results(result.get("ResumeResults" if active else "SuspendResults") or [])

    async def set_ad_archived(self, account: str, ad_ref: str, archived: bool) -> None:
        ad_id = int(ad_ref.removeprefix("ad:"))
        method = "archive" if archived else "unarchive"
        result = await self._call(account, "ads", method, {"SelectionCriteria": {"Ids": [ad_id]}})
        key = "ArchiveResults" if archived else "UnarchiveResults"
        check_results(result.get(key) or [])

    async def _moderate(self, account: str, ads: Sequence[Mapping[str, Any]]) -> None:
        drafts = [int(ad["Id"]) for ad in ads if ad.get("Status") == "DRAFT"]
        if not drafts:
            return
        result = await self._call(
            account, "ads", "moderate", {"SelectionCriteria": {"Ids": drafts}}
        )
        check_results(result.get("ModerateResults") or [])

    async def _add_text_ads(
        self,
        account: str,
        group_id: int,
        ads: Sequence[tuple[str, str, str]],
        image_hash: str | None = None,
    ) -> list[int]:
        if not ads:
            return []
        # Mobile помечен устаревшим; Директ принимает и NO, и отсутствие поля
        # (проверено 2026-11-08).
        image = {"AdImageHash": image_hash} if image_hash else {}
        result = await self._call(
            account,
            "ads",
            "add",
            {
                "Ads": [
                    {
                        "AdGroupId": group_id,
                        "TextAd": {
                            "Title": title,
                            "Text": text,
                            "Href": href,
                            "Mobile": "NO",
                            **image,
                        },
                    }
                    for title, text, href in ads
                ]
            },
        )
        return check_results(result.get("AddResults") or [])

    async def _keywords(self, account: str, group_id: int) -> list[str]:
        """Существующие фразы группы объявлений."""
        result = await self._call(
            account,
            "keywords",
            "get",
            {
                "SelectionCriteria": {"AdGroupIds": [group_id]},
                "FieldNames": ["Id", "Keyword"],
            },
        )
        return [str(kw["Keyword"]) for kw in (result.get("Keywords") or []) if "Keyword" in kw]

    async def _add_keywords(
        self,
        account: str,
        group_id: int,
        keywords: Sequence[str],
    ) -> list[int]:
        """Добавляет недостающие ключевые фразы и условие автотаргетинга в группу."""
        if not keywords:
            return []
        present = {kw.lower().strip() for kw in await self._keywords(account, group_id)}
        missing = [kw for kw in keywords if kw.lower().strip() not in present]
        if not missing:
            return []
        # Лимит Директа общий на группу, включая уже добавленные вручную фразы.
        missing = missing[: max(0, MAX_KEYWORDS_PER_GROUP - len(present))]
        if not missing:
            return []

        added_ids: list[int] = []
        # Директ принимает до 1000 фраз в одном вызове
        for i in range(0, len(missing), 1000):
            batch = missing[i : i + 1000]
            items: list[dict[str, Any]] = []
            for kw in batch:
                item: dict[str, Any] = {
                    "AdGroupId": group_id,
                    "Keyword": kw,
                }
                if kw == "---autotargeting":
                    item["AutotargetingCategories"] = [
                        {"Category": "EXACT", "Value": "YES"},
                        {"Category": "ALTERNATIVE", "Value": "YES"},
                        {"Category": "ACCESSORY", "Value": "YES"},
                    ]
                items.append(item)
            result = await self._call(
                account,
                "keywords",
                "add",
                {"Keywords": items},
            )
            added_ids.extend(check_results(result.get("AddResults") or []))
        return added_ids

    async def find_ad(self, account: str, marker: str) -> PublishedAd | None:
        campaign_ids = [int(c["Id"]) for c in await self._campaigns(account)]
        for ad in await self._ads_of_campaigns(account, campaign_ids):
            text_ad = ad.get("TextAd") or {}
            href, found = unmark_href(str(text_ad.get("Href") or ""))
            if found == marker:
                return PublishedAd(
                    ad_id=str(ad["Id"]),
                    campaign_id=str(ad["CampaignId"]),
                    headline=str(text_ad.get("Title") or ""),
                    text=str(text_ad.get("Text") or ""),
                    href=href,
                    group_id=str(ad.get("AdGroupId") or ""),
                    image_hash=str(text_ad["AdImageHash"]) if text_ad.get("AdImageHash") else None,
                )
        return None

    async def ad_group(self, account: str, ad_id: str) -> str | None:
        found = await self._ads(account, {"Ids": [int(ad_id)]})
        return str(found[0]["AdGroupId"]) if found and found[0].get("AdGroupId") else None

    async def create_ad(
        self,
        account: str,
        campaign_id: str,
        marker: str,
        headline: str,
        text: str,
        href: str,
        group_id: str | None = None,
        image_hash: str | None = None,
        group_name: str | None = None,
        keywords: Sequence[str] = (),
    ) -> None:
        groups = await self._groups(account, int(campaign_id))
        ids = [int(group["Id"]) for group in groups]
        if not ids:
            raise DirectRejected(None, f"campaign {campaign_id} has no ad groups")
        if group_id is not None:
            # Вариант A/B-теста встаёт в группу контроля: Директ делит показы внутри группы.
            if int(group_id) not in ids:
                raise DirectRejected(None, f"group {group_id} is not in campaign {campaign_id}")
            target = int(group_id)
        else:
            # У Яндекса лимит относится к неархивным объявлениям группы. Приостановленное
            # объявление продолжает занимать слот; архивное — нет. После трёх создаём группу.
            counts: dict[int, int] = {}
            for group in groups:
                group_ref = int(group["Id"])
                existing = await self._ads(account, {"AdGroupIds": [group_ref]})
                counts[group_ref] = sum(ad.get("State") != "ARCHIVED" for ad in existing)
            group_name = (group_name or "").strip()
            wanted_name = group_name or f"Дополнительные объявления {campaign_id}"
            named_prefix = f"AH {wanted_name[:180]} "
            same = [g for g in groups if str(g.get("Name") or "").startswith(named_prefix)]
            target = next(
                (
                    int(g["Id"])
                    for g in same
                    if counts[int(g["Id"])] < MAX_COMBINATORIAL_ADS_PER_GROUP
                ),
                0,
            )
            if not target and not group_name:
                target = next(
                    (
                        group_ref
                        for group_ref in ids
                        if counts[group_ref] < MAX_COMBINATORIAL_ADS_PER_GROUP
                    ),
                    0,
                )
            if not target:
                # «Общие услуги» остаются в базовой группе; именованные направления получают
                # отдельную группу даже при наличии свободных слотов в общей.
                if group_name == "Общие услуги":
                    target = next(
                        (
                            group_ref
                            for group_ref in ids
                            if counts[group_ref] < MAX_COMBINATORIAL_ADS_PER_GROUP
                        ),
                        0,
                    )
                if not target:
                    base = next((g for g in groups if counts[int(g["Id"])] > 0), groups[0])
                    suffix = len(same) + 1
                    name = f"{named_prefix}{suffix}"[:255]
                    result = await self._call(
                        account,
                        "adgroups",
                        "add",
                        {
                            "AdGroups": [
                                {
                                    "Name": name,
                                    "CampaignId": int(campaign_id),
                                    "RegionIds": list(base.get("RegionIds") or [0]),
                                    "UnifiedAdGroup": {"OfferRetargeting": "NO"},
                                }
                            ]
                        },
                    )
                    target = check_results(result.get("AddResults") or [])[0]
                    group_keywords = list(keywords) or await self._keywords(
                        account, int(base["Id"])
                    )
                    if group_keywords:
                        await self._add_keywords(account, target, group_keywords)
            elif keywords:
                await self._add_keywords(account, target, keywords)
        added = await self._add_text_ads(
            account, target, [(headline, text, mark_href(href, marker))], image_hash
        )
        # Новое объявление — черновик; показы начнутся после проверки Директа.
        await self._moderate(account, await self._ads(account, {"Ids": added}))

    async def upload_image(self, account: str, image: AdImage) -> str:
        """AdImages.add: одинаковые картинки Директ хранит один раз, повтор вернёт тот же номер."""
        result = await self._call(
            account,
            "adimages",
            "add",
            {
                "AdImages": [
                    {
                        "ImageData": base64.b64encode(image.data).decode("ascii"),
                        "Name": image.name[:255],
                    }
                ]
            },
        )
        found = list(result.get("AddResults") or [])
        check_results(found)
        if not found or not found[0].get("AdImageHash"):
            raise DirectError(None, "no AdImageHash in adimages.add")
        return str(found[0]["AdImageHash"])

    async def moderation(self, account: str, ad_ids: Sequence[str]) -> list[ModerationVerdict]:
        if not ad_ids:
            return []
        found = {
            str(ad["Id"]): ad for ad in await self._ads(account, {"Ids": [int(a) for a in ad_ids]})
        }
        verdicts = []
        for ad_id in ad_ids:
            ad = found.get(ad_id, {})
            state = MODERATION_STATES.get(str(ad.get("Status")), ModerationState.MODERATION)
            clarification = str(ad.get("StatusClarification") or "")
            reasons = (
                (clarification,) if state is ModerationState.REJECTED and clarification else ()
            )
            verdicts.append(ModerationVerdict(ad_id, state, reasons))
        return verdicts

    # --- Где и когда показывать ---

    async def _groups(self, account: str, campaign_id: int) -> list[Mapping[str, Any]]:
        result = await self._call(
            account,
            "adgroups",
            "get",
            {
                "SelectionCriteria": {"CampaignIds": [campaign_id]},
                "FieldNames": ["Id", "CampaignId", "Name", "RegionIds"],
            },
        )
        return sorted(result.get("AdGroups") or [], key=lambda group: int(group["Id"]))

    async def read_targeting(self, account: str, campaign_id: str) -> Targeting | None:
        found = await self._campaigns(account, [int(campaign_id)], extra=["TimeTargeting"])
        if not found:
            return None
        groups = await self._groups(account, int(campaign_id))
        regions = sorted({int(r) for group in groups for r in group.get("RegionIds") or []})
        return Targeting(parse_schedule(found[0].get("TimeTargeting")), tuple(regions))

    async def set_schedule(self, account: str, campaign_id: str, schedule: Schedule) -> None:
        result = await self._call(
            account,
            "campaigns",
            "update",
            {
                "Campaigns": [
                    {
                        "Id": int(campaign_id),
                        "TimeTargeting": {
                            "Schedule": {"Items": schedule_items(schedule)},
                            "ConsiderWorkingWeekends": "YES",
                        },
                    }
                ]
            },
        )
        check_results(result.get("UpdateResults") or [])

    async def set_regions(self, account: str, campaign_id: str, regions: tuple[int, ...]) -> None:
        groups = await self._groups(account, int(campaign_id))
        if not groups:
            raise DirectRejected(None, f"campaign {campaign_id} has no ad groups")
        result = await self._call(
            account,
            "adgroups",
            "update",
            {"AdGroups": [{"Id": int(g["Id"]), "RegionIds": list(regions)} for g in groups]},
        )
        check_results(result.get("UpdateResults") or [])

    # --- Запуск ---

    async def _launch_campaign(self, account: str, marker: str) -> Mapping[str, Any] | None:
        tag = f"({marker})"
        for campaign in await self._campaigns(account):
            # Архивная кампания не показывает объявления: повтор создаёт новую, а не доделывает эту.
            if tag in str(campaign.get("Name", "")) and campaign.get("State") != "ARCHIVED":
                return campaign
        return None

    async def find_campaign(self, account: str, marker: str) -> CreatedCampaign | None:
        campaign = await self._launch_campaign(account, marker)
        if campaign is None:
            return None
        campaign_id = int(campaign["Id"])
        strategy = self._strategy(campaign)
        groups = await self._groups(account, campaign_id)
        ads = [
            ad
            for ad in await self._ads_of_campaigns(account, [campaign_id])
            if ad.get("State") != "ARCHIVED"
        ]
        found_budgets = budgets(strategy)
        end_date = next((str(b["EndDate"]) for _, _, b in found_budgets if b.get("EndDate")), None)
        counters = ((campaign.get("UnifiedCampaign") or {}).get("CounterIds") or {}).get(
            "Items"
        ) or []
        text_ads = [ad.get("TextAd") or {} for ad in ads]
        restored = NewCampaign(
            marker=marker,
            name=str(campaign["Name"]),
            placement=placement_of(strategy),
            limit=period_limit(strategy),
            ends_at=ends_at_of(end_date) if end_date else datetime.min.replace(tzinfo=UTC),
            regions=tuple(sorted({int(r) for g in groups for r in g.get("RegionIds") or []})),
            href=unmark_href(str(text_ads[0].get("Href") or ""))[0] if text_ads else "",
            counters=tuple(int(c) for c in counters),
            ads=tuple(NewAd(str(t.get("Title") or ""), str(t.get("Text") or "")) for t in text_ads),
        )
        return CreatedCampaign(str(campaign_id), restored, tuple(str(ad["Id"]) for ad in ads))

    def _strategy_for(self, campaign: NewCampaign, start: date) -> dict[str, Any]:
        # Своя автостратегия у поиска и у сетей сразу Директ отвергает («Стратегии не
        # совместимы», 2026-11-05): при «поиск и сайты» сети идут по стратегии поиска.
        side = "Network" if campaign.placement is Placement.NETWORKS else "Search"
        other = "Search" if side == "Network" else "Network"
        follows = NETWORK_DEFAULT if campaign.placement is Placement.ALL else SERVING_OFF
        strategy: dict[str, Any] = {other: {"BiddingStrategyType": follows}}
        part = campaign.limit
        strategy[side] = {
            "BiddingStrategyType": "WB_MAXIMUM_CLICKS",
            "WbMaximumClicks": {
                "CustomPeriodBudget": {
                    "SpendLimit": part,
                    "StartDate": start.isoformat(),
                    "EndDate": last_day_of(campaign.ends_at).isoformat(),
                    # Период не продлевается сам: без сервиса показы остановятся.
                    "AutoContinue": "NO",
                }
            },
        }
        return strategy

    async def create_campaign(self, account: str, campaign: NewCampaign) -> None:
        """Доделывает недостающее: кампанию, группу, объявления, отправку на проверку."""
        existing = await self._launch_campaign(account, campaign.marker)
        if existing is None:
            start = _today(self._clock())
            unified: dict[str, Any] = {"BiddingStrategy": self._strategy_for(campaign, start)}
            if campaign.counters:
                unified["CounterIds"] = {"Items": list(campaign.counters)}
            result = await self._call(
                account,
                "campaigns",
                "add",
                {
                    "Campaigns": [
                        {
                            "Name": campaign.name,
                            "StartDate": start.isoformat(),
                            "EndDate": last_day_of(campaign.ends_at).isoformat(),
                            "UnifiedCampaign": unified,
                        }
                    ]
                },
            )
            campaign_id = check_results(result.get("AddResults") or [])[0]
        else:
            campaign_id = int(existing["Id"])
        groups = await self._groups(account, campaign_id)
        if not groups:
            result = await self._call(
                account,
                "adgroups",
                "add",
                {
                    "AdGroups": [
                        {
                            "Name": campaign.name[:255],
                            "CampaignId": campaign_id,
                            "RegionIds": list(campaign.regions),
                            "UnifiedAdGroup": {"OfferRetargeting": "NO"},
                        }
                    ]
                },
            )
            check_results(result.get("AddResults") or [])
            groups = await self._groups(account, campaign_id)
        if not groups:
            raise DirectRejected(None, f"campaign {campaign_id} has no ad groups")

        existing_ads = await self._ads_of_campaigns(account, [campaign_id])
        present = {
            (str((ad.get("TextAd") or {}).get("Title")), str((ad.get("TextAd") or {}).get("Text")))
            for ad in existing_ads
            if ad.get("State") != "ARCHIVED"
        }
        counts: dict[int, int] = {}
        for group in groups:
            group_ref = int(group["Id"])
            items = await self._ads(account, {"AdGroupIds": [group_ref]})
            counts[group_ref] = sum(ad.get("State") != "ARCHIVED" for ad in items)

        async def create_group(label: str, ordinal: int) -> int:
            base = groups[0]
            name = f"AH {label} {ordinal}"[:255]
            result = await self._call(
                account,
                "adgroups",
                "add",
                {
                    "AdGroups": [
                        {
                            "Name": name,
                            "CampaignId": campaign_id,
                            "RegionIds": list(base.get("RegionIds") or campaign.regions),
                            "UnifiedAdGroup": {"OfferRetargeting": "NO"},
                        }
                    ]
                },
            )
            group_ref = check_results(result.get("AddResults") or [])[0]
            groups.append({"Id": group_ref, "Name": name, "RegionIds": base.get("RegionIds")})
            counts[group_ref] = 0
            return group_ref

        href = mark_href(campaign.href, campaign.marker)
        for ad in campaign.ads:
            if (ad.headline, ad.text) in present:
                continue
            label = (ad.group.strip() or "Общие услуги")[:180]
            named_prefix = f"AH {label} "
            matching = [g for g in groups if str(g.get("Name") or "").startswith(named_prefix)]
            if label == "Общие услуги":
                target = next(
                    (
                        int(g["Id"])
                        for g in groups
                        if counts[int(g["Id"])] < MAX_COMBINATORIAL_ADS_PER_GROUP
                    ),
                    0,
                )
            else:
                target = next(
                    (
                        int(g["Id"])
                        for g in matching
                        if counts[int(g["Id"])] < MAX_COMBINATORIAL_ADS_PER_GROUP
                    ),
                    0,
                )
            if not target:
                target = await create_group(label, len(matching) + 1)
            group_keywords = ad.keywords or campaign.keywords
            if group_keywords:
                await self._add_keywords(account, target, group_keywords)
            await self._add_text_ads(account, target, [(ad.headline, ad.text, href)])
            present.add((ad.headline, ad.text))
            counts[target] += 1
        await self._moderate(account, await self._ads_of_campaigns(account, [campaign_id]))

    # --- Маркировка (AdvertiserRegistry) ---

    async def read_advertiser(self, account: str) -> Advertiser | None:
        """Читает данные рекламодателя из Директа (Clients.get)."""
        result = await self._call(
            account,
            "clients",
            "get",
            {
                "FieldNames": ["ClientId", "Login", "ClientInfo", "Phone"],
                "TinInfoFieldNames": ["TinType", "Tin"],
                "OrganizationFieldNames": ["Name", "OkvedCode"],
            },
        )
        clients = result.get("Clients")
        if not isinstance(clients, list) or not clients or not isinstance(clients[0], Mapping):
            return None
        client = clients[0]
        tin_info = client.get("TinInfo")
        if not isinstance(tin_info, Mapping):
            return None
        tin = tin_info.get("Tin")
        if not tin or not isinstance(tin, str):
            return None
        tin_type = str(tin_info.get("TinType", ""))
        kind_map = {
            "LEGAL": "legal",
            "INDIVIDUAL": "ip",
            "PHYSICAL": "person",
        }
        kind = kind_map.get(tin_type, tin_type.lower())
        name = str(client.get("ClientInfo") or "")
        phone = client.get("Phone")
        phone_str = str(phone) if phone else None
        erir = client.get("ErirAttributes") or {}
        org = erir.get("Organization") if isinstance(erir, Mapping) else {}
        okved = org.get("OkvedCode") if isinstance(org, Mapping) else None
        return Advertiser(
            kind=kind,
            inn=tin,
            name=name,
            phone=phone_str,
            okved=str(okved) if okved else None,
        )

    async def set_advertiser(self, account: str, advertiser: Advertiser) -> None:
        """Обновляет реквизиты рекламодателя в Директе (Clients.update)."""
        tin_type_map = {
            "legal": "LEGAL",
            "ip": "INDIVIDUAL",
            "person": "PHYSICAL",
        }
        tin_type = tin_type_map.get(advertiser.kind, advertiser.kind.upper())
        client_data: dict[str, Any] = {
            "ClientInfo": advertiser.name,
            "TinInfo": {
                "TinType": tin_type,
                "Tin": advertiser.inn,
            },
        }
        if advertiser.phone:
            client_data["Phone"] = advertiser.phone
        if advertiser.okved:
            client_data["ErirAttributes"] = {
                "Organization": {
                    "Name": advertiser.name,
                    "OkvedCode": advertiser.okved,
                }
            }
        result = await self._call(
            account,
            "clients",
            "update",
            {"Clients": [client_data]},
        )
        check_results(result.get("UpdateResults") or [])
