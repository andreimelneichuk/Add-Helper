from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from adhelper.experiments.domain.model import (
    ExperimentRecord,
    ExperimentRules,
    ExperimentStore,
    Hypothesis,
    ModerationState,
    Outcome,
    Status,
    TestAlreadyRunning,
    VariantStats,
    active_variant,
    evaluate,
    transition,
)

__all__ = [
    "ExperimentService",
    "Hypothesis",
    "ModerationState",
    "Observation",
    "TestAlreadyRunning",
]

DATA_REASON = "data_untrusted"
OUTSIDE_REASON = "changed_outside_service"


@dataclass(frozen=True)
class Observation:
    control: VariantStats
    variant: VariantStats
    spent: int
    data_trusted: bool = True


class AdSwitch(Protocol):
    async def set_active(
        self, account: str, ad_ref: str, active: bool, explanation: str, key: str
    ) -> bool: ...
    async def record_external(
        self, account: str, ad_ref: str, expected: bool, actual: bool, explanation: str, key: str
    ) -> None: ...


class AdReader(Protocol):
    async def read_ad(self, account: str, ad_ref: str) -> Mapping[str, Any]: ...


class ExperimentService:
    """Ход теста. Все включения и выключения идут через журнал."""

    def __init__(
        self,
        store: ExperimentStore,
        switch: AdSwitch,
        ads: AdReader,
        clock: Callable[[], datetime],
        rules: ExperimentRules,
    ) -> None:
        self._store = store
        self._switch = switch
        self._ads = ads
        self._clock = clock
        self._rules = rules

    async def get(self, experiment_id: int) -> ExperimentRecord:
        record = await self._store.get(experiment_id)
        if record is None:
            raise LookupError("Тест не найден")
        return record

    async def start(
        self,
        account: str,
        campaign_id: str,
        control_ref: str,
        variant_ref: str,
        hypothesis: Hypothesis,
    ) -> int:
        return await self._store.add(
            account, campaign_id, control_ref, variant_ref, hypothesis.value, self._rules.version
        )

    async def moderation(self, experiment_id: int, state: ModerationState) -> None:
        record = await self.get(experiment_id)
        if state is ModerationState.REJECTED:
            transition(record.status, Status.CANCELLED)
            await self._store.set_status(
                experiment_id, Status.CANCELLED, reason="moderation_rejected"
            )
            return
        transition(record.status, Status.RUNNING)
        await self._store.set_status(experiment_id, Status.RUNNING, started_at=self._clock())

    async def resume(self, experiment_id: int) -> None:
        """Пользователь решил продолжить: состояние объявлений приводим к расписанию."""
        record = await self.get(experiment_id)
        transition(record.status, Status.RUNNING)
        await self._store.set_status(experiment_id, Status.RUNNING)
        await self._store.begin_switch(experiment_id, active_variant(self._day(record)))

    def _day(self, record: ExperimentRecord) -> int:
        if record.started_at is None:
            raise ValueError("Тест ещё не запущен")
        return (self._clock() - record.started_at).days

    @staticmethod
    def _refs(record: ExperimentRecord, target: str) -> tuple[str, str]:
        """Включаемое и выключаемое объявление."""
        if target == "B":
            return record.variant_ref, record.control_ref
        return record.control_ref, record.variant_ref

    async def _switch_to(self, record: ExperimentRecord, target: str) -> bool:
        """Сначала включаем, потом выключаем: группа не должна оставаться без показов."""
        on, off = self._refs(record, target)
        prefix = f"experiment:{record.id}:{record.switch_seq}"
        text = f"A/B-тест: сегодня работает вариант {target}, это часть чередования."
        for ref, active in ((on, True), (off, False)):
            if not await self._switch.set_active(
                record.account, ref, active, text, f"{prefix}:{ref}:{active}"
            ):
                return False
        await self._store.finish_switch(record.id, target)
        return True

    async def _changed_outside(self, record: ExperimentRecord) -> bool:
        on, off = self._refs(record, record.expected)
        changed = False
        for ref, expected in ((on, True), (off, False)):
            actual = (await self._ads.read_ad(record.account, ref)).get("active")
            if actual is not None and actual != expected:
                await self._switch.record_external(
                    record.account,
                    ref,
                    expected,
                    actual,
                    "Объявление изменили в кабинете во время теста. Тест приостановлен.",
                    f"external:{record.id}:{record.switch_seq}:{ref}",
                )
                changed = True
        return changed

    async def tick(self, experiment_id: int, observation: Observation) -> None:
        record = await self.get(experiment_id)
        if record.status is Status.PAUSED and record.reason == DATA_REASON:
            if not observation.data_trusted:
                return
            await self._store.set_status(experiment_id, Status.RUNNING)
            record = await self.get(experiment_id)
        if record.status is not Status.RUNNING:
            return
        if not observation.data_trusted:
            await self._store.set_status(experiment_id, Status.PAUSED, reason=DATA_REASON)
            return
        if record.switch_to is not None:
            if not await self._switch_to(record, record.switch_to):
                return
            record = await self.get(experiment_id)
        elif await self._changed_outside(record):
            await self._store.set_status(experiment_id, Status.PAUSED, reason=OUTSIDE_REASON)
            return

        day = self._day(record)
        result = evaluate(
            observation.control, observation.variant, day, observation.spent, self._rules
        )
        if result.outcome is not Outcome.CONTINUE and result.winner is not None:
            await self._store.begin_switch(experiment_id, result.winner)
            record = await self.get(experiment_id)
            if await self._switch_to(record, result.winner):
                await self._store.set_status(
                    experiment_id,
                    Status.APPLIED,
                    winner=result.winner,
                    explanation=result.explanation,
                )
            return
        desired = active_variant(day)
        if desired != record.expected:
            await self._store.begin_switch(experiment_id, desired)
            await self._switch_to(await self.get(experiment_id), desired)
