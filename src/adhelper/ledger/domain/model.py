from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ChangeKind(StrEnum):
    SET_LIMIT = "set_limit"
    PAUSE = "pause"
    RESUME = "resume"
    PAUSE_AD = "pause_ad"
    RESUME_AD = "resume_ad"
    SET_SCHEDULE = "set_schedule"
    SET_REGIONS = "set_regions"
    # Новая кампания запуска. Отмены нет: созданное останавливают паузой, а не удалением.
    CREATE_CAMPAIGN = "create_campaign"
    # Новое объявление в работающей кампании: замена, ремонт после отказа или место в направлении.
    # Отмены нет: объявление останавливают приостановкой, а не удалением.
    CREATE_AD = "create_ad"
    # Данные рекламодателя для маркировки. Отмены нет: без них показ без маркировки незаконен.
    SET_ADVERTISER = "set_advertiser"


class ChangeSource(StrEnum):
    AUTOPILOT = "autopilot"
    USER = "user"
    ROLLBACK = "rollback"
    BUDGET_GUARD = "budget_guard"
    EXPERIMENT = "experiment"
    EXTERNAL = "external"


class ChangeStatus(StrEnum):
    PLANNED = "planned"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    REJECTED = "rejected"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class ProposedChange:
    account: str
    source: ChangeSource
    kind: ChangeKind
    target_ref: str
    planned: Mapping[str, Any]
    explanation: str
    idempotency_key: str
    decision_id: str | None = None
    reverts_change_id: int | None = None


@dataclass(frozen=True)
class Change:
    id: int
    account: str
    source: ChangeSource
    kind: ChangeKind
    target_ref: str
    before: Mapping[str, Any]
    planned: Mapping[str, Any]
    after: Mapping[str, Any] | None
    status: ChangeStatus
    explanation: str
    attempts: int
    failure_reason: str | None
    decision_id: str | None = None
    reverts_change_id: int | None = None
    idempotency_key: str | None = None


class ExecutionStep(StrEnum):
    APPLY = "apply"
    FINISH = "finish"
    CONFLICT = "conflict"
    GIVE_UP = "give_up"


def plan_reached(planned: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    return all(actual.get(key) == value for key, value in planned.items())


def decide_execution(
    before: Mapping[str, Any],
    planned: Mapping[str, Any],
    actual: Mapping[str, Any],
    attempts: int,
    max_attempts: int,
) -> ExecutionStep:
    """Что делать по факту на площадке. Повтор вслепую невозможен: решение всегда после чтения."""
    if plan_reached(planned, actual):
        return ExecutionStep.FINISH
    if any(actual.get(key) != before.get(key) for key in planned):
        return ExecutionStep.CONFLICT
    if attempts >= max_attempts:
        return ExecutionStep.GIVE_UP
    return ExecutionStep.APPLY
