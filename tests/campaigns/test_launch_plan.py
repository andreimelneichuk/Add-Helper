"""План запуска: из бюджета и выбранных объявлений — кампании с лимитами в рамках бюджета."""

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from adhelper.budget.domain.limits import BudgetPeriod
from adhelper.campaigns.adapters.rules_file import load_launch_rules
from adhelper.campaigns.domain.launch import (
    LaunchProblem,
    NewAd,
    NewCampaign,
    Placement,
    plan_launch,
    region_of,
)

RULES = load_launch_rules("launch-1")
STARTS = datetime(2026, 11, 1, 21, 0, tzinfo=UTC)
ENDS = datetime(2026, 12, 1, 21, 0, tzinfo=UTC)
ADS = (NewAd("Маникюр на Тверской", "Маникюр за 1700 ₽: Москва, ул. Тверская, 7."),)
MICROS = 1_000_000


def _period(rubles: int, reserve_bp: int = 1000) -> BudgetPeriod:
    return BudgetPeriod(rubles * MICROS, reserve_bp, STARTS, ENDS)


def test_small_budget_gives_one_campaign_within_operating_budget() -> None:
    plan = plan_launch(
        "k1",
        "Ноготок",
        _period(20_000),
        ADS,
        "https://nogti.ru/",
        "Москва, ул. Тверская, 7",
        (98765432,),
        RULES,
    )
    assert plan.problem is None
    [campaign] = plan.campaigns
    # Рабочий бюджет 18 000 ₽: резерв 10% не раздаём.
    assert campaign.limit == 18_000 * MICROS
    assert campaign.placement is Placement.ALL
    assert campaign.ends_at == ENDS
    assert campaign.regions == (213,)
    assert campaign.counters == (98765432,)
    # Метка в имени: по ней повтор найдёт уже созданную кампанию.
    assert campaign.marker in campaign.name and campaign.target_ref.startswith("launch:")


def test_large_budget_splits_search_and_networks() -> None:
    plan = plan_launch(
        "k1",
        "Ноготок",
        _period(60_000),
        ADS,
        "https://nogti.ru/",
        "Москва, ул. Тверская, 7",
        (),
        RULES,
    )
    assert [c.placement for c in plan.campaigns] == [Placement.SEARCH, Placement.NETWORKS]
    assert sum(c.limit for c in plan.campaigns) <= _period(60_000).operating_budget
    assert len({c.marker for c in plan.campaigns}) == 2


def test_budget_below_platform_minimum_is_refused_with_minimum() -> None:
    plan = plan_launch(
        "k1", "Ноготок", _period(1_550), ADS, "https://nogti.ru/", "Москва", (), RULES
    )
    assert plan.problem is LaunchProblem.BELOW_PLATFORM_MIN
    assert plan.campaigns == ()
    # 50 ₽ в день на 30 дней после резерва 10%: не меньше 1 667 ₽ в месяц.
    assert plan.minimum_monthly == 1_667 * MICROS


def test_launch_needs_site_ads_and_known_city() -> None:
    base = ("k1", "Ноготок", _period(20_000))
    assert plan_launch(*base, (), "https://nogti.ru/", "Москва", (), RULES).problem is (
        LaunchProblem.NO_ADS
    )
    assert plan_launch(*base, ADS, "", "Москва", (), RULES).problem is LaunchProblem.NO_SITE
    assert plan_launch(*base, ADS, "https://nogti.ru/", "ул. Ленина, 5", (), RULES).problem is (
        LaunchProblem.NO_CITY
    )


def test_city_is_found_by_whole_word() -> None:
    assert region_of("г. Санкт-Петербург, Невский пр., 1", RULES) == 2
    assert region_of("Москва, ул. Тверская, 7", RULES) == 213
    assert region_of("Казань", RULES) == 43
    # «Москворецкая» не Москва: город ищем целым словом.
    assert region_of("Тула, ул. Москворецкая, 3", RULES) is None


def test_planned_survives_the_journal() -> None:
    [campaign] = plan_launch(
        "k1", "Ноготок", _period(20_000), ADS, "https://nogti.ru/", "Москва", (1,), RULES
    ).campaigns
    assert NewCampaign.from_planned(campaign.marker, campaign.planned()) == campaign


@given(
    rubles=st.integers(min_value=0, max_value=10_000_000),
    reserve_bp=st.integers(min_value=0, max_value=10_000),
)
def test_limits_never_exceed_operating_budget(rubles: int, reserve_bp: int) -> None:
    period = _period(rubles, reserve_bp)
    plan = plan_launch("k1", "Ноготок", period, ADS, "https://nogti.ru/", "Москва", (), RULES)
    total = sum(c.limit for c in plan.campaigns)
    assert total <= period.operating_budget
    minimum = RULES.min_daily_rubles * RULES.period_days * MICROS
    if plan.problem is None:
        assert plan.campaigns and all(c.limit >= minimum for c in plan.campaigns)
        assert all(c.limit % MICROS == 0 for c in plan.campaigns)
    else:
        assert plan.campaigns == ()
