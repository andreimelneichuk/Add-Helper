"""План запуска: какие кампании создать и с какими лимитами.

Решение о деньгах детерминировано: лимиты считает код по правилам, сумма лимитов не выходит
за рабочий бюджет периода. Создаёт кампании журнал, по одной записи на кампанию.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.budget.domain.rules import BASIS_POINTS

# Цель записи журнала о создании: кампании ещё нет, её находят по нашей метке в имени.
LAUNCH_PREFIX = "launch:"
MICROS_PER_RUB = 1_000_000


@dataclass(frozen=True)
class LaunchRules:
    version: str
    period_days: int
    # Минимум площадки на кампанию в день, в рублях.
    min_daily_rubles: int
    # С какого бюджета в месяц поиск и сети идут отдельными кампаниями.
    split_from_monthly_rubles: int
    max_ads_per_campaign: int
    # Город в адресе → номер региона Яндекса. Ищем целым словом.
    cities: Mapping[str, int]


class Placement(StrEnum):
    ALL = "all"
    SEARCH = "search"
    NETWORKS = "networks"


PLACEMENT_WORDS = {
    Placement.ALL: "поиск и сайты",
    Placement.SEARCH: "поиск",
    Placement.NETWORKS: "сайты и приложения",
}


class LaunchProblem(StrEnum):
    NO_ADS = "no_ads"
    NO_SITE = "no_site"
    NO_CITY = "no_city"
    BELOW_PLATFORM_MIN = "below_platform_min"


class UnregisteredAdvertiserError(Exception):
    """Попытка запустить рекламу без подтверждённых реквизитов рекламодателя (ИНН)."""


def sanitize_direct_text(raw: str) -> str:
    """Очищает заголовок или текст объявления от символов, запрещённых в Директе (ошибка 5002).

    Убирает математические знаки (±, ≈, ≠), эмодзи и нормализует пробелы.
    Префиксный символ ± заменяется на «около» («±964 г» -> «около 964 г»): на сайте
    это приблизительный вес, и объявление не должно утверждать точный.
    Инфиксный символ ± между числами превращается в диапазон («500±50 г» -> «500-50 г»).
    Символы сравнения ≤ и ≥ заменяются на одиночные < и > (без удлинения строки).
    """
    if not raw:
        return ""
    cleaned = raw.replace("\u00a0", " ")
    cleaned = re.sub(r"[\r\n\t]+", " ", cleaned)
    cleaned = re.sub(r"(\d)\s*±\s*(\d)", r"\1-\2", cleaned)
    cleaned = re.sub(r"±\s*(?=\d)", "около ", cleaned)
    cleaned = re.sub(r"±\s*", "", cleaned)
    cleaned = re.sub(r"[≈≠~]", "", cleaned)
    cleaned = (
        cleaned.replace("≤", "<")
        .replace("≥", ">")
        .replace("÷", "/")
        .replace("×", "x")
        .replace("•", "-")
    )
    cleaned = re.sub(r"[\ufe00-\ufe0f]", "", cleaned)
    cleaned = re.sub(r"[\U00010000-\U0010ffff]", "", cleaned)
    cleaned = re.sub(r"[\u2600-\u27bf\u2b00-\u2bff]", "", cleaned)
    cleaned = re.sub(r" +", " ", cleaned)
    cleaned = re.sub(r" +([.,!?:;])", r"\1", cleaned)
    return cleaned.strip()


@dataclass(frozen=True)
class NewAd:
    headline: str
    text: str
    group: str = "Общие услуги"
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "headline", sanitize_direct_text(self.headline))
        object.__setattr__(self, "text", sanitize_direct_text(self.text))


@dataclass(frozen=True)
class NewCampaign:
    marker: str
    name: str
    placement: Placement
    limit: int
    ends_at: datetime
    regions: tuple[int, ...]
    href: str
    counters: tuple[int, ...]
    ads: tuple[NewAd, ...]
    keywords: tuple[str, ...] = ()
    # Минус-слова кампании: отсекают запросы, которые не приводят покупателей.
    negative_keywords: tuple[str, ...] = ()

    @property
    def target_ref(self) -> str:
        return f"{LAUNCH_PREFIX}{self.marker}"

    def planned(self) -> dict[str, Any]:
        """Что должно оказаться на площадке. Чтение обратно возвращает те же поля."""
        data: dict[str, Any] = {
            "name": self.name,
            "placement": self.placement.value,
            "limit": self.limit,
            "ends_at": self.ends_at.isoformat(),
            "regions": list(self.regions),
            "href": self.href,
            "counters": list(self.counters),
            "ads": [
                {
                    "headline": ad.headline,
                    "text": ad.text,
                    **({"group": ad.group} if ad.group != "Общие услуги" else {}),
                    **({"keywords": list(ad.keywords)} if ad.keywords else {}),
                }
                for ad in self.ads
            ],
        }
        if self.keywords:
            data["keywords"] = list(self.keywords)
        data["negative_keywords"] = list(self.negative_keywords)
        return data

    @staticmethod
    def from_planned(marker: str, planned: Mapping[str, Any]) -> "NewCampaign":
        return NewCampaign(
            marker=marker,
            name=str(planned["name"]),
            placement=Placement(planned["placement"]),
            limit=int(planned["limit"]),
            ends_at=datetime.fromisoformat(planned["ends_at"]),
            regions=tuple(int(r) for r in planned["regions"]),
            href=str(planned["href"]),
            counters=tuple(int(c) for c in planned["counters"]),
            ads=tuple(
                NewAd(
                    str(a["headline"]),
                    str(a["text"]),
                    str(a.get("group") or "Общие услуги"),
                    tuple(str(k) for k in a.get("keywords", ())),
                )
                for a in planned["ads"]
            ),
            keywords=tuple(str(k) for k in planned.get("keywords", ())),
            negative_keywords=tuple(str(k) for k in planned.get("negative_keywords", ())),
        )


@dataclass(frozen=True)
class LaunchPlan:
    campaigns: tuple[NewCampaign, ...]
    problem: LaunchProblem | None = None
    # При нехватке бюджета: с какой суммы в месяц запуск возможен, в микроединицах.
    minimum_monthly: int | None = None


def region_of(address: str, rules: LaunchRules) -> int | None:
    """Город из адреса профиля. Длинные названия первыми: «Санкт-Петербург» раньше «Петербург»."""
    text = address.lower().replace("ё", "е")
    for city in sorted(rules.cities, key=len, reverse=True):
        pattern = rf"(?<![\w-]){re.escape(city.lower().replace('ё', 'е'))}(?![\w-])"
        if re.search(pattern, text):
            return rules.cities[city]
    return None


def minimum_campaign_limit(rules: LaunchRules) -> int:
    return rules.min_daily_rubles * rules.period_days * MICROS_PER_RUB


def minimum_monthly(reserve_bp: int, rules: LaunchRules) -> int:
    """Наименьший бюджет в месяц, рабочая часть которого покрывает минимум одной кампании."""
    need = minimum_campaign_limit(rules)
    share = BASIS_POINTS - reserve_bp
    if share <= 0:
        return 0
    micros = -(-need * BASIS_POINTS // share)
    return -(-micros // MICROS_PER_RUB) * MICROS_PER_RUB


def plan_launch(
    launch_key: str,
    title: str,
    period: BudgetPeriod,
    ads: Sequence[NewAd],
    href: str,
    address: str,
    counters: Sequence[int],
    rules: LaunchRules,
    keywords: Sequence[str] = (),
    region_id: int | None = None,
    negative_keywords: Sequence[str] = (),
) -> LaunchPlan:
    if not ads:
        return LaunchPlan((), LaunchProblem.NO_ADS)
    if not href:
        return LaunchPlan((), LaunchProblem.NO_SITE)
    region = region_id if region_id is not None else region_of(address, rules)
    if region is None:
        return LaunchPlan((), LaunchProblem.NO_CITY)
    if period.ends_at is None:
        raise ValueError("У периода запуска должна быть дата окончания")
    operating = period.operating_budget
    floor = minimum_campaign_limit(rules)
    split = (
        period.hard_limit >= rules.split_from_monthly_rubles * MICROS_PER_RUB
        and _whole_rubles(operating // 2) >= floor
    )
    placements = (Placement.SEARCH, Placement.NETWORKS) if split else (Placement.ALL,)
    # Округляем вниз до рубля: сумма лимитов остаётся в рамках рабочего бюджета.
    limit = _whole_rubles(operating // len(placements))
    if limit < floor or limit <= 0:
        return LaunchPlan(
            (),
            LaunchProblem.BELOW_PLATFORM_MIN,
            minimum_monthly(period.reserve_bp, rules),
        )
    # Лимит действует на группу, а площадка позволяет создавать новые группы кампании.
    chosen = tuple(ads)
    campaigns = tuple(
        NewCampaign(
            marker=f"ah-{launch_key}-{index + 1}",
            name=f"{title}: {PLACEMENT_WORDS[placement]} (ah-{launch_key}-{index + 1})",
            placement=placement,
            limit=limit,
            ends_at=period.ends_at,
            regions=(region,),
            href=href,
            counters=tuple(counters),
            ads=chosen,
            keywords=tuple(keywords),
            negative_keywords=tuple(negative_keywords),
        )
        for index, placement in enumerate(placements)
    )
    return LaunchPlan(campaigns)


def _whole_rubles(micros: int) -> int:
    return micros // MICROS_PER_RUB * MICROS_PER_RUB
