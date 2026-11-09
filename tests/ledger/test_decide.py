from adhelper.ledger.domain.model import ExecutionStep, decide_execution

BEFORE = {"limit": 500}
PLANNED = {"limit": 400}


def test_not_applied_yet_is_applied() -> None:
    assert decide_execution(BEFORE, PLANNED, {"limit": 500}, 0, 3) is ExecutionStep.APPLY


def test_fact_matches_plan_finishes_without_applying() -> None:
    assert decide_execution(BEFORE, PLANNED, {"limit": 400}, 1, 3) is ExecutionStep.FINISH


def test_plan_compares_only_planned_fields() -> None:
    actual = {"limit": 400, "active": True}
    assert decide_execution(BEFORE, PLANNED, actual, 1, 3) is ExecutionStep.FINISH


def test_external_edit_is_a_conflict() -> None:
    assert decide_execution(BEFORE, PLANNED, {"limit": 450}, 0, 3) is ExecutionStep.CONFLICT


def test_attempts_are_bounded() -> None:
    assert decide_execution(BEFORE, PLANNED, {"limit": 500}, 3, 3) is ExecutionStep.GIVE_UP


def test_fact_matching_plan_wins_over_attempt_limit() -> None:
    assert decide_execution(BEFORE, PLANNED, {"limit": 400}, 3, 3) is ExecutionStep.FINISH


def test_partly_created_object_is_resumed_not_a_conflict() -> None:
    # Регрессия: создание без «до» видело собственную недоделанную кампанию как чужую правку,
    # и изменение зависало в «конфликте». Объект находит метка, значит он наш.
    partial = {"name": "Кампания", "ads": []}
    planned = {"name": "Кампания", "ads": [{"headline": "А", "text": "Б"}]}
    assert decide_execution({}, planned, partial, 1, 3) is ExecutionStep.APPLY
