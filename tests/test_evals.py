"""Набор оценки и подсчёт метрик. Модель не вызывается."""

from evals.cases import CASES, PROFILES
from evals.score import CaseRun, metrics, single_call_average

from adhelper.creatives.adapters.rules_file import load_rules
from adhelper.creatives.domain.checks import check_draft
from adhelper.creatives.domain.model import ViolationKind

RULES = load_rules("creatives-1")


def test_cases_are_consistent() -> None:
    ids = [case.id for case in CASES]
    assert len(ids) == len(set(ids))
    assert all(case.profile in PROFILES for case in CASES)
    assert {case.label for case in CASES} <= {"pass", "block", "ambiguous"}
    labels = [case.label for case in CASES]
    assert labels.count("pass") >= 20 and labels.count("block") >= 40


def test_cases_fit_platform_limits() -> None:
    # Иначе код снимал бы примеры за длину, и замер смешивал бы длину со смыслом.
    for case in CASES:
        kinds = {v.kind for v in check_draft(case.draft, PROFILES[case.profile], RULES)}
        assert ViolationKind.TOO_LONG not in kinds, case.id
        assert ViolationKind.EMPTY not in kinds, case.id


def test_both_splits_have_both_labels() -> None:
    for split in ("dev", "holdout", "fresh"):
        labels = {case.label for case in CASES if case.split == split}
        assert {"pass", "block"} <= labels


def test_metrics_count_misses_and_false_blocks() -> None:
    by_id = {case.id: case for case in CASES}
    runs = [
        CaseRun(by_id["salon-05"], (False, False, True), code_blocked=False),  # пропуск
        CaseRun(by_id["salon-08"], (True, True, True), code_blocked=True),
        CaseRun(by_id["salon-01"], (True, False, False), code_blocked=False),
        CaseRun(by_id["cafe-03"], (False, False, False), code_blocked=True),
        CaseRun(by_id["salon-15"], (True, True, True), code_blocked=False),  # спорный
    ]
    majority = metrics(runs, "majority")
    assert (majority.caught, majority.violations) == (1, 2)
    assert majority.missed_ids == ("salon-05",)
    assert majority.false_blocks == 0
    first = metrics(runs, "single:0")
    assert first.false_block_ids == ("salon-01",)
    assert metrics(runs, "code").false_block_ids == ("cafe-03",)
    assert metrics(runs, "combined").false_blocks == 2
    recall, false_block = single_call_average(runs)
    assert round(recall) == 67 and round(false_block) == 17
