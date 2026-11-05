"""История кабинета для симулятора: только свой бизнес, визиты неизвестны, лимиты кампаний."""

from datetime import timedelta

from adhelper.autopilot.adapters.data import SqlHistory
from tests.autopilot.test_data import (  # noqa: F401  — тот же набор данных и фикстура
    BUSINESS,
    OTHER,
    TODAY,
    Sessions,
    _seed,
    ad,
    stats,
)


async def test_history_is_days_of_own_business_in_order(sessions: Sessions) -> None:
    history = await SqlHistory(sessions, BUSINESS).load("acc")

    assert len(history.days) == 30
    assert history.days[0].day == TODAY - timedelta(days=29)
    assert history.days[-1].day == TODAY
    [line] = history.days[0].ads
    assert (line.ref, line.campaign_id, line.group_id) == ("ad:1", "c1", "ad:1")
    assert (line.spend, line.leads, line.clicks, line.visits) == (100_000_000, 1, 20, None)
    assert history.limits == {"c1": 800_000_000}


async def test_second_ad_joins_the_same_days(sessions: Sessions) -> None:
    await ad(sessions, BUSINESS, "2", "c1")
    await stats(sessions, BUSINESS, "2", 3)

    history = await SqlHistory(sessions, BUSINESS).load("acc")

    assert [len(day.ads) for day in history.days[-4:]] == [1, 2, 2, 2]


async def test_unknown_account_has_no_history(sessions: Sessions) -> None:
    history = await SqlHistory(sessions, OTHER).load("нет-такого")
    assert history.days == [] and history.limits == {}
