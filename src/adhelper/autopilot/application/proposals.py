"""Ответ владельца на предложение теста.

Предложение — запись в журнале решений; ответ — новая запись, старые не меняются.
"""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from adhelper.autopilot.application.cycle import DecisionLog, TestPlanner

PROPOSED = "awaiting_confirmation"
START_TEST = "start_test"


class Answer(StrEnum):
    PREPARED = "prepared"
    DECLINED = "declined"
    TEST_RUNNING = "test_running"
    STALE = "stale"
    NOT_FOUND = "not_found"


class Decision(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def account(self) -> str: ...
    @property
    def object_ref(self) -> str: ...
    @property
    def verdict(self) -> str: ...
    @property
    def action(self) -> str | None: ...
    @property
    def status(self) -> str: ...
    @property
    def inputs(self) -> Mapping[str, Any]: ...
    @property
    def rules_version(self) -> str: ...


class Decisions(DecisionLog, Protocol):
    async def get(self, decision_id: int) -> Decision | None: ...
    async def latest(self, account: str, object_ref: str) -> Decision | None: ...


async def answer_test(
    decisions: Decisions, tests: TestPlanner, decision_id: int, agree: bool, now: datetime
) -> Answer:
    """Отвечают только на действующее предложение: повтор и старая карточка ничего не делают."""
    proposal = await decisions.get(decision_id)
    if proposal is None or proposal.action != START_TEST:
        return Answer.NOT_FOUND
    current = await decisions.latest(proposal.account, proposal.object_ref)
    if current is None or current.id != proposal.id or current.status != PROPOSED:
        return Answer.STALE
    inputs = dict(proposal.inputs)

    async def record(status: str) -> int:
        return await decisions.record(
            proposal.account,
            proposal.object_ref,
            proposal.verdict,
            proposal.action,
            status,
            inputs,
            proposal.rules_version,
            now,
        )

    if not agree:
        await record(Answer.DECLINED.value)
        return Answer.DECLINED
    status = await tests.prepare(
        proposal.account,
        str(inputs["campaign_id"]),
        proposal.object_ref,
        str(inputs["hypothesis"]),
        proposal.id,
    )
    await record(status)
    return Answer(status)


async def owner_cancelled(
    decisions: Decisions, account: str, object_ref: str, now: datetime
) -> bool:
    """Владелец отменил готовящийся тест: записываем отказ, чтобы цикл не предложил его снова."""
    current = await decisions.latest(account, object_ref)
    if current is None or current.action != START_TEST:
        return False
    await decisions.record(
        account,
        object_ref,
        current.verdict,
        current.action,
        Answer.DECLINED.value,
        {**current.inputs, "cancelled_by_owner": True},
        current.rules_version,
        now,
    )
    return True
