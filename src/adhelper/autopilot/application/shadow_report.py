"""Недельный отчёт теневого периода: собирает метрики, выносит вердикт воротам, пишет текст.

Площадку не трогает и моделей не вызывает (docs/17-autopilot-plan.md, этап 8).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from adhelper.autopilot.domain.shadow_report import (
    BASIS_POINTS,
    GateLine,
    GateStatus,
    GateThresholds,
    WeekMetrics,
    evaluate_gates,
    pick_sample,
    share_bp,
    sum_metrics,
    week_seed,
    week_start,
)

REPORT_DAYS = 7


class SampleLine(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def business_id(self) -> UUID: ...
    @property
    def object_ref(self) -> str: ...
    @property
    def action(self) -> str | None: ...
    @property
    def status(self) -> str: ...
    @property
    def rules_version(self) -> str: ...
    @property
    def decided_at(self) -> datetime: ...


class MetricsSource(Protocol):
    async def metrics(
        self, start: datetime, end: datetime, ticks: tuple[int, int] = (0, 0)
    ) -> WeekMetrics: ...
    async def sample_pool(self, start: datetime, end: datetime) -> Sequence[SampleLine]: ...


class Ticks(Protocol):
    async def missed(self, start: datetime, end: datetime) -> tuple[int, int]: ...


@dataclass(frozen=True)
class WeekReport:
    seed: str
    start: datetime
    end: datetime
    thresholds: GateThresholds
    per_business: Mapping[UUID, WeekMetrics]
    total: WeekMetrics
    gates: Sequence[GateLine]
    sample: Sequence[SampleLine]


async def build_report(
    sources: Mapping[UUID, MetricsSource],
    ticks: Ticks,
    now: datetime,
    thresholds: GateThresholds,
    days: int | None = None,
) -> WeekReport:
    """По умолчанию прошлая полная неделя по Москве: два запуска в одну неделю дают один отчёт.

    С `days` окно скользящее, до текущего часа: для проверки «за последние N суток». Порог
    пропущенных тиков задан на неделю и пересчитывается на длину окна (округление вверх).
    """
    if days is not None and days < 1:
        raise ValueError("Окно отчёта не короче одних суток")
    if days is None:
        end = week_start(now)
        start = end - timedelta(days=REPORT_DAYS)
        seed = week_seed(start)
        window_days = REPORT_DAYS
    else:
        end = now.replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=days)
        seed = week_seed(now)
        window_days = days
    scaled = -(-thresholds.max_missed_ticks * window_days // REPORT_DAYS)
    gate_thresholds = replace(thresholds, max_missed_ticks=scaled)
    missed = await ticks.missed(start, end)
    per_business = {b: await source.metrics(start, end) for b, source in sources.items()}
    total = sum_metrics([*per_business.values(), _ticks_only(missed)])
    pool: list[SampleLine] = []
    for source in sources.values():
        pool += await source.sample_pool(start, end)
    chosen = set(pick_sample([line.id for line in pool], seed))
    sample = sorted((line for line in pool if line.id in chosen), key=lambda line: line.id)
    return WeekReport(
        seed,
        start,
        end,
        thresholds,
        per_business,
        total,
        evaluate_gates(total, gate_thresholds),
        sample,
    )


def _ticks_only(missed: tuple[int, int]) -> WeekMetrics:
    return WeekMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, missed[0], missed[1], 0)


def _percent(bp: int | None) -> str:
    """Округление только при показе."""
    return "данных нет" if bp is None else f"{(bp + 50) // 100}%"


GATE_TEXT = {
    "rollbacks": "Автооткаты среди самостоятельных действий",
    "accepted": "Предложения, которые приняли",
    "worker": "Пропущенные часовые запуски воркера",
}
STATUS_TEXT = {
    GateStatus.OK: "в норме",
    GateStatus.FAIL: "хуже порога",
    GateStatus.NO_DATA: "данных мало",
    GateStatus.UNCONFIRMED: "порог ещё не подтверждён владельцем",
}


def _gate_line(line: GateLine) -> str:
    if line.name == "worker":
        shown = "данных нет" if line.value_bp is None else str(line.value_bp)
        return (
            f"- {GATE_TEXT[line.name]}: {shown}, допустимо не больше {line.limit_bp}"
            f" ({STATUS_TEXT[line.status]})"
        )
    limit = "не больше" if line.name == "rollbacks" else "не меньше"
    return (
        f"- {GATE_TEXT[line.name]}: {_percent(line.value_bp)}, порог {limit} "
        f"{_percent(line.limit_bp)} ({STATUS_TEXT[line.status]})"
    )


def render_text(report: WeekReport) -> list[str]:
    total = report.total
    start, end = report.start.strftime("%d.%m.%Y"), report.end.strftime("%d.%m.%Y")
    lines = [
        f"Отчёт теневого периода за {start} — {end} (пороги {report.thresholds.version}).",
        f"Кабинетов: {len(report.per_business)}.",
        f"Предложений владельцам: {total.proposals}, принято {total.accepted}"
        f" ({_percent(share_bp(total.accepted, total.proposals))}).",
        f"Самостоятельных действий: {total.auto_actions}, автооткатов {total.auto_rollbacks}"
        f" ({_percent(share_bp(total.auto_rollbacks, total.auto_actions))}).",
        f"Оценено исходов: {total.judged}, хуже контроля {total.worse}"
        f" ({_percent(share_bp(total.worse, total.judged))}), данных мало {total.insufficient}.",
        f"Расхождения autopilot-2 и autopilot-3: {total.diverged} из {total.shadow_pairs}"
        f" ({_percent(share_bp(total.diverged, total.shadow_pairs))}).",
        f"Воркер: ждали {total.ticks_expected} часовых запусков, пропущено {total.ticks_missed}.",
        f"Платных вызовов рецензента: {total.reviewed_actions} (у правил денег моделей нет).",
        "Ворота этапа 9:",
        *(_gate_line(line) for line in report.gates),
    ]
    if not report.thresholds.confirmed_by_owner:
        lines.append("Пороги ворот пока значения по умолчанию: их задаёт владелец проекта.")
    lines.append(
        f"Для разбора глазами выбрано решений: {len(report.sample)} (зерно {report.seed})."
    )
    for item in report.sample:
        lines.append(
            f"- №{item.id}, {item.decided_at.strftime('%d.%m %H:%M')} UTC, {item.object_ref},"
            f" действие {item.action or 'нет'}, статус {item.status}, правила {item.rules_version}"
        )
    return lines


__all__ = ["BASIS_POINTS", "MetricsSource", "WeekReport", "build_report", "render_text"]
