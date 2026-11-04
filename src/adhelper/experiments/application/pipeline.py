"""Тест по итогам анализа доходит до показов: docs/04-autopilot.md §5 «Ход теста».

Готовящийся тест получает вариант через журнал (`create_ad`, источник `experiment`), и вариант
сразу выключается: до старта показывается только контроль, как ждёт расписание. Итог
проверки Яндекса запускает или отменяет тест, дальше чередование и анализ ведёт
`ExperimentService.tick`. Повтор безопасен: созданное находится по метке, переключения
сравнивают состояние с расписанием.
"""

import logging
from collections.abc import Sequence
from typing import Protocol

from adhelper.budget.domain.ports import PlatformUnavailable
from adhelper.creatives.domain.model import AdDraft, BusinessProfile, CreativeRules
from adhelper.experiments.application.service import ExperimentService, Observation
from adhelper.experiments.domain.model import (
    ExperimentRecord,
    Hypothesis,
    ModerationState,
    Status,
)
from adhelper.experiments.domain.variant import variant_draft
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import (
    Change,
    ChangeKind,
    ChangeSource,
    ChangeStatus,
    ProposedChange,
)
from adhelper.ledger.domain.ports import GatewayUnavailable
from adhelper.platforms.domain.ports import AdPublisher
from adhelper.platforms.domain.ports import ModerationState as Verdict

logger = logging.getLogger(__name__)

NEW_AD_PREFIX = "newad:"
AD_PREFIX = "ad:"
NO_VARIANT = "no_variant"


class VariantContext(Protocol):
    async def control(self, account: str, ad_id: str) -> tuple[AdDraft, str] | None: ...
    async def profile(self) -> BusinessProfile | None: ...


class Observations(Protocol):
    async def observe(self, record: ExperimentRecord) -> Observation: ...


class VariantStore(Protocol):
    async def open_for(self, account: str) -> list[ExperimentRecord]: ...
    async def set_variant(self, experiment_id: int, variant_ref: str) -> None: ...
    async def set_status(
        self, experiment_id: int, status: Status, reason: str | None = None
    ) -> None: ...


def marker(experiment_id: int) -> str:
    return f"ahx-{experiment_id}"


def _latest_applied(rows: Sequence[Change], kind: ChangeKind) -> Change | None:
    done = [row for row in rows if row.kind is kind and row.status is ChangeStatus.APPLIED]
    return max(done, key=lambda row: row.id) if done else None


async def _apply(ledger: Ledger, proposed: ProposedChange) -> Change | None:
    """Запись через журнал; применённое находим по цели, повтор начинается с чтения."""
    done = _latest_applied(
        await ledger.changes_on_target(proposed.account, proposed.target_ref), proposed.kind
    )
    if done is not None:
        return done
    await ledger.submit(proposed)
    await ledger.execute_pending(proposed.account)
    return _latest_applied(
        await ledger.changes_on_target(proposed.account, proposed.target_ref), proposed.kind
    )


async def _create_variant(
    record: ExperimentRecord,
    store: VariantStore,
    ledger: Ledger,
    context: VariantContext,
    rules: CreativeRules,
) -> None:
    control = await context.control(record.account, record.control_ref.removeprefix(AD_PREFIX))
    profile = await context.profile()
    draft = None
    if control is not None and profile is not None:
        draft = variant_draft(profile, control[0], Hypothesis(record.hypothesis), rules)
    if control is None or draft is None:
        # Честно: вариант по гипотезе из профиля не собрать, тест не начинается.
        await store.set_status(record.id, Status.CANCELLED, reason=NO_VARIANT)
        return
    created = await _apply(
        ledger,
        ProposedChange(
            account=record.account,
            source=ChangeSource.EXPERIMENT,
            kind=ChangeKind.CREATE_AD,
            target_ref=f"{NEW_AD_PREFIX}{marker(record.id)}",
            planned={
                "campaign_id": record.campaign_id,
                "headline": draft.headline,
                "text": draft.text,
                "href": control[1],
            },
            explanation=(
                f"Отправил на проверку Яндексу вариант «{draft.headline}» для A/B-теста."
                " Почему: прежнее объявление на границе цели, проверяем одну перемену."
                " Дальше: после одобрения варианты чередуются по дням."
                " Отменить: тест можно остановить в «Сегодня»."
            ),
            idempotency_key=f"experiment-variant:{record.id}",
        ),
    )
    if created is None or not created.after or "ad_id" not in created.after:
        return
    variant = f"{AD_PREFIX}{created.after['ad_id']}"
    held = await _apply(
        ledger,
        ProposedChange(
            account=record.account,
            source=ChangeSource.EXPERIMENT,
            kind=ChangeKind.PAUSE_AD,
            target_ref=variant,
            planned={"active": False},
            explanation=(
                "Вариант ждёт старта теста выключенным: пока показывается прежнее объявление."
            ),
            idempotency_key=f"experiment:{record.id}:hold",
        ),
    )
    if held is not None:
        await store.set_variant(record.id, variant)


async def _check_variant(
    record: ExperimentRecord, service: ExperimentService, publisher: AdPublisher
) -> None:
    assert record.variant_ref is not None
    ad_id = record.variant_ref.removeprefix(AD_PREFIX)
    try:
        verdicts = await publisher.moderation(record.account, [ad_id])
    except PlatformUnavailable:
        return
    for verdict in verdicts:
        if verdict.ad_id != ad_id:
            continue
        if verdict.state is Verdict.ACCEPTED:
            await service.moderation(record.id, ModerationState.APPROVED)
        elif verdict.state is Verdict.REJECTED:
            await service.moderation(record.id, ModerationState.REJECTED)


async def run_experiments(
    account: str,
    store: VariantStore,
    service: ExperimentService,
    ledger: Ledger,
    publisher: AdPublisher,
    context: VariantContext,
    observations: Observations,
    rules: CreativeRules,
) -> None:
    """Один проход по открытым тестам кабинета."""
    for record in await store.open_for(account):
        try:
            if record.status is Status.REVIEW and record.variant_ref is None:
                await _create_variant(record, store, ledger, context, rules)
            elif record.status is Status.REVIEW:
                await _check_variant(record, service, publisher)
            else:
                await service.tick(record.id, await observations.observe(record))
        except GatewayUnavailable:
            # Запись осталась в журнале: следующий проход доведёт её чтением.
            logger.warning("Тест %s: площадка не ответила, продолжим позже", record.id)
            return
