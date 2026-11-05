"""Визиты Метрики в данных автопилота: docs/04-autopilot.md §3, проверка качества данных.

Визиты приходят через порт `SiteVisits`. Нет счётчика, нет данных или Метрика не ответила —
визиты неизвестны (None), а не ноль: проверки визитов пропускаются, вывод о слежке не делается.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.application.visits import WithVisits
from adhelper.autopilot.domain.health import HealthInput, HealthReason, Window, check_health
from adhelper.autopilot.domain.model import AccountData
from adhelper.budget.domain.ports import PlatformUnavailable

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
RULES = load_rules("autopilot-2")
RECENT = Window(2_000_000_000, 9, 1000, None)
BASELINE = Window(10_000_000_000, 50, 5000, None)


@dataclass
class Inner:
    async def load(self, account: str, now: datetime) -> AccountData:
        return AccountData([], [], RECENT, BASELINE, False, False)


@dataclass
class Metrika:
    answer: int | None = 900
    down: bool = False
    calls: list[tuple[str, date, date]] = field(default_factory=list)

    async def ad_visits(self, counter: str, since: date, until: date) -> int | None:
        self.calls.append((counter, since, until))
        if self.down:
            raise PlatformUnavailable("metrika")
        return self.answer


def _counter(value: str | None):  # type: ignore[no-untyped-def]
    async def counter() -> str | None:
        return value

    return counter


async def _load(metrika: Metrika, counter: str | None = "98765432") -> AccountData:
    return await WithVisits(Inner(), metrika, _counter(counter), RULES).load("acc", NOW)


def _reasons(data: AccountData) -> tuple[HealthReason, ...]:
    health = HealthInput(data.recent, data.baseline, False, False)
    return check_health(health, RULES).reasons


async def test_visits_for_recent_window_come_from_metrika_by_moscow_days() -> None:
    metrika = Metrika(answer=900)
    data = await _load(metrika)
    assert data.recent.visits == 900
    # Окно — последние 7 московских дней, включая сегодняшний, как у отчётов.
    assert metrika.calls == [("98765432", date(2026, 11, 14), date(2026, 11, 20))]
    assert _reasons(data) == ()


async def test_zero_visits_with_clicks_means_tracking_lost() -> None:
    data = await _load(Metrika(answer=0))
    assert _reasons(data) == (HealthReason.TRACKING_LOST,)


async def test_few_visits_for_clicks_means_mismatch() -> None:
    data = await _load(Metrika(answer=100))
    assert _reasons(data) == (HealthReason.VISITS_MISMATCH,)


async def test_no_data_in_metrika_is_unknown_not_zero() -> None:
    data = await _load(Metrika(answer=None))
    assert data.recent.visits is None
    assert _reasons(data) == ()


async def test_metrika_down_is_unknown_and_cycle_goes_on() -> None:
    data = await _load(Metrika(down=True))
    assert data.recent.visits is None


async def test_without_counter_metrika_is_not_asked() -> None:
    metrika = Metrika()
    data = await _load(metrika, counter=None)
    assert metrika.calls == []
    assert data.recent.visits is None
