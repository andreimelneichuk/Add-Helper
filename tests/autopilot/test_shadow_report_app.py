"""Сборка и вывод недельного отчёта на подставных источниках."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from adhelper.autopilot.application.shadow_report import build_report, render_text
from adhelper.autopilot.domain.shadow_report import GateThresholds, WeekMetrics

NOW = datetime(2026, 11, 16, 7, 40, tzinfo=UTC)
THRESHOLDS = GateThresholds("gates-1", 1000, 6000, 2, False)


@dataclass(frozen=True)
class Row:
    id: int
    business_id: uuid.UUID
    object_ref: str = "ad:1"
    action: str | None = "pause_ad"
    status: str = "applied"
    rules_version: str = "autopilot-2"
    decided_at: datetime = NOW


class Source:
    def __init__(self, business: uuid.UUID, metrics: WeekMetrics, ids: range) -> None:
        self._m, self._business, self._ids = metrics, business, ids

    async def metrics(
        self, start: datetime, end: datetime, ticks: tuple[int, int] = (0, 0)
    ) -> WeekMetrics:
        assert end - start == (end - start) and start < end
        return self._m

    async def sample_pool(self, start: datetime, end: datetime) -> list[Row]:
        return [Row(i, self._business) for i in self._ids]


class Ticks:
    async def missed(self, start: datetime, end: datetime) -> tuple[int, int]:
        return 168, 1


def metrics(**changes: int) -> WeekMetrics:
    base = dict(proposals=4, accepted=3, auto_actions=2, auto_rollbacks=0, judged=2, worse=0,
                insufficient=1, shadow_pairs=10, diverged=2, ticks_expected=0, ticks_missed=0,
                reviewed_actions=0)  # fmt: skip
    return WeekMetrics(**(base | changes))


async def test_report_sums_businesses_and_takes_ticks_once() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    sources = {
        a: Source(a, metrics(), range(1, 8)),
        b: Source(b, metrics(proposals=6, accepted=1), range(10, 16)),
    }

    report = await build_report(sources, Ticks(), NOW, THRESHOLDS)

    assert (report.total.proposals, report.total.accepted) == (10, 4)
    assert (report.total.ticks_expected, report.total.ticks_missed) == (168, 1)
    assert len(report.sample) == 10 and len({r.id for r in report.sample}) == 10
    again = await build_report(sources, Ticks(), NOW, THRESHOLDS)
    assert [r.id for r in again.sample] == [r.id for r in report.sample]


async def test_text_is_in_owner_words_and_says_thresholds_are_not_confirmed() -> None:
    a = uuid.uuid4()
    report = await build_report({a: Source(a, metrics(), range(1, 4))}, Ticks(), NOW, THRESHOLDS)

    text = "\n".join(render_text(report))

    assert "принято 3 (75%)" in text
    assert "Расхождения autopilot-2 и autopilot-3: 2 из 10 (20%)" in text
    assert "ждали 168 часовых запусков, пропущено 1" in text
    assert "задаёт владелец проекта" in text and "порог ещё не подтверждён" in text
    assert text.count("\n- №") == 3


async def test_no_businesses_still_makes_a_report() -> None:
    report = await build_report({}, Ticks(), NOW, THRESHOLDS)

    assert report.sample == [] and "данных нет" in "\n".join(render_text(report))
