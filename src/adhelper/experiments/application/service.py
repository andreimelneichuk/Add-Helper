from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

from adhelper.experiments.domain.model import (
    BOTH,
    ExperimentRecord,
    ExperimentRules,
    ExperimentStore,
    Hypothesis,
    IllegalTransition,
    ModerationState,
    Observation,
    Outcome,
    Status,
    TestAlreadyRunning,
    evaluate,
    pick_hypothesis,
    supported_hypotheses,
    transition,
    wanted,
)

__all__ = [
    "ExperimentService",
    "Hypothesis",
    "IllegalTransition",
    "ModerationState",
    "Observation",
    "TestAlreadyRunning",
    "cancel_preparing",
    "pick_hypothesis",
    "supported_hypotheses",
]

DATA_REASON = "data_untrusted"
OUTSIDE_REASON = "changed_outside_service"
OWNER_REASON = "owner_cancelled"
# Причина выключения проигравшего: по ней хранитель освобождает место (docs/05 §8).
LOST_TEST = "lost_test"
SIDE_NAME = {"A": "прежний вариант", "B": "новый вариант"}


async def cancel_preparing(store: ExperimentStore, experiment_id: int) -> ExperimentRecord:
    """Отмена владельцем до начала: объявления ещё не переключались, журнал не нужен.

    Идущий тест уже переключал показы, его отмена — через журнал, и она пока не сделана.
    """
    record = await store.get(experiment_id)
    if record is None:
        raise LookupError("Тест не найден")
    if record.status is not Status.REVIEW:
        raise IllegalTransition(f"{record.status.value} -> cancelled by owner")
    await store.set_status(experiment_id, Status.CANCELLED, reason=OWNER_REASON)
    return record


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
    """Ход теста. Все включения и выключения идут через журнал.

    Варианты стоят в одной группе и показываются одновременно: Яндекс сам делит показы,
    сервис включает вариант на старте и выключает проигравшего по итогу. Суточных пауз нет.
    """

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

    async def prepare(
        self, account: str, campaign_id: str, control_ref: str, hypothesis: Hypothesis
    ) -> int:
        """Тест по итогам анализа: направление занято, вариант напишут и создадут позже.

        Показы не меняются, пока вариант не одобрен модерацией.
        """
        return await self._store.add(
            account, campaign_id, control_ref, None, hypothesis.value, self._rules.version
        )

    async def cancel(self, experiment_id: int) -> None:
        await cancel_preparing(self._store, experiment_id)

    async def moderation(self, experiment_id: int, state: ModerationState) -> None:
        record = await self.get(experiment_id)
        if record.variant_ref is None and state is ModerationState.APPROVED:
            raise ValueError("У теста ещё нет варианта: запускать нечего")
        if state is ModerationState.REJECTED:
            transition(record.status, Status.CANCELLED)
            await self._store.set_status(
                experiment_id,
                Status.CANCELLED,
                reason="moderation_rejected",
                finished_at=self._clock(),
            )
            return
        transition(record.status, Status.RUNNING)
        await self._store.set_status(experiment_id, Status.RUNNING, started_at=self._clock())
        # Вариант ждал выключенным: теперь встаёт рядом с контролем. Не вышло — доведёт tick.
        await self._store.begin_switch(experiment_id, BOTH)
        await self._switch_to(await self.get(experiment_id), BOTH)

    async def resume(self, experiment_id: int) -> None:
        """Пользователь решил продолжить: оба варианта снова показываются."""
        record = await self.get(experiment_id)
        transition(record.status, Status.RUNNING)
        await self._store.set_status(experiment_id, Status.RUNNING)
        await self._store.begin_switch(experiment_id, BOTH)

    def _day(self, record: ExperimentRecord) -> int:
        if record.started_at is None:
            raise ValueError("Тест ещё не запущен")
        return (self._clock() - record.started_at).days

    @staticmethod
    def _refs(record: ExperimentRecord) -> dict[str, str]:
        if record.variant_ref is None:
            raise ValueError("У теста ещё нет варианта")
        return {"A": record.control_ref, "B": record.variant_ref}

    def _explain(self, target: str, side: str, active: bool, verdict: str) -> tuple[str, str]:
        """Объяснение и метка записи журнала для одного объявления."""
        if target == BOTH and active:
            return (
                "A/B-тест начался: новый вариант показывается одновременно с прежним,"
                " Яндекс сам делит показы между ними. Почему: Яндекс одобрил вариант."
                f" Дальше: итог через {self._rules.min_days}–{self._rules.max_days} дней,"
                " когда наберётся данных. Отменить: выключите вариант в истории.",
                "on",
            )
        if target == BOTH:
            return ("A/B-тест: оба варианта показываются одновременно.", "on")
        if active:
            return (f"A/B-тест закончен, {SIDE_NAME[side]} остаётся. {verdict}", "winner")
        return (
            f"Проиграло A/B-тест: {verdict} Объявление приостановлено, а не удалено:"
            " включить его снова можно в истории.",
            LOST_TEST,
        )

    async def _switch_to(self, record: ExperimentRecord, target: str, verdict: str = "") -> bool:
        """Приводит объявления к цели. Повтор начинается с чтения: совпавшее не трогаем."""
        refs = self._refs(record)
        for side, active in wanted(target).items():
            ref = refs[side]
            if (await self._ads.read_ad(record.account, ref)).get("active") == active:
                continue
            text, label = self._explain(target, side, active, verdict)
            key = f"experiment:{record.id}:{record.switch_seq}:{label}:{ref}"
            if not await self._switch.set_active(record.account, ref, active, text, key):
                return False
        await self._store.finish_switch(record.id, target)
        return True

    async def _changed_outside(self, record: ExperimentRecord) -> bool:
        refs = self._refs(record)
        changed = False
        for side, expected in wanted(record.expected).items():
            ref = refs[side]
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

    async def _finish(self, record: ExperimentRecord) -> None:
        """Итог уже решён и записан: победитель остаётся, проигравший выключается."""
        winner = record.winner
        assert winner is not None
        if record.switch_to != winner:
            await self._store.begin_switch(record.id, winner)
            record = await self.get(record.id)
        if await self._switch_to(record, winner, record.explanation or ""):
            await self._store.set_status(
                record.id,
                Status.APPLIED,
                winner=winner,
                explanation=record.explanation,
                finished_at=self._clock(),
                rules_version=self._rules.version,
            )

    async def tick(self, experiment_id: int, observation: Observation) -> None:
        record = await self.get(experiment_id)
        if record.status is Status.PAUSED and record.reason == DATA_REASON:
            if not observation.data_trusted:
                return
            await self._store.set_status(experiment_id, Status.RUNNING)
            record = await self.get(experiment_id)
        if record.status is not Status.RUNNING:
            return
        if record.winner is not None:
            # Решение принято раньше, но площадка не ответила: доводим его, новых данных не ждём.
            await self._finish(record)
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
        if record.expected != BOTH:
            # Тест начат на чередовании (`experiments-1`): оба варианта встают рядом.
            await self._store.begin_switch(experiment_id, BOTH)
            await self._switch_to(await self.get(experiment_id), BOTH)
            return

        result = evaluate(
            observation.control,
            observation.variant,
            self._day(record),
            observation.spent,
            self._rules,
        )
        if result.outcome is Outcome.CONTINUE or result.winner is None:
            return
        # Сначала решение в запись теста, потом площадка: повтор доведёт то же решение.
        await self._store.set_status(
            experiment_id, Status.RUNNING, winner=result.winner, explanation=result.explanation
        )
        await self._finish(await self.get(experiment_id))
