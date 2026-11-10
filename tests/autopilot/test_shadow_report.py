"""Недельный отчёт теневого периода: числа, ворота этапа 9, выбор случаев для разбора."""

from datetime import UTC, datetime

import pytest

from adhelper.autopilot.domain.shadow_report import (
    GateStatus,
    GateThresholds,
    WeekMetrics,
    count_divergence,
    evaluate_gates,
    pick_sample,
    share_bp,
    week_seed,
)

THRESHOLDS = GateThresholds(
    version="gates-1",
    max_rollback_share_bp=1000,
    min_accepted_share_bp=6000,
    max_missed_ticks=2,
    confirmed_by_owner=True,
)


def metrics(**changes: int) -> WeekMetrics:
    base = {
        "proposals": 10,
        "accepted": 7,
        "auto_actions": 10,
        "auto_rollbacks": 1,
        "judged": 8,
        "worse": 1,
        "insufficient": 2,
        "shadow_pairs": 20,
        "diverged": 3,
        "ticks_expected": 168,
        "ticks_missed": 0,
        "reviewed_actions": 0,
    }
    return WeekMetrics(**(base | changes))


def test_share_is_integer_basis_points_and_none_without_base() -> None:
    assert share_bp(1, 3) == 3333
    assert share_bp(0, 5) == 0
    assert share_bp(1, 0) is None


def test_all_gates_pass_when_numbers_inside_thresholds() -> None:
    lines = {line.name: line for line in evaluate_gates(metrics(), THRESHOLDS)}
    assert {line.status for line in lines.values()} == {GateStatus.OK}


def test_rollback_share_over_limit_fails() -> None:
    lines = {line.name: line for line in evaluate_gates(metrics(auto_rollbacks=3), THRESHOLDS)}
    assert lines["rollbacks"].status is GateStatus.FAIL


def test_low_acceptance_fails_and_no_proposals_is_no_data_not_pass() -> None:
    low = {line.name: line for line in evaluate_gates(metrics(accepted=2), THRESHOLDS)}
    assert low["accepted"].status is GateStatus.FAIL
    none = {
        line.name: line for line in evaluate_gates(metrics(proposals=0, accepted=0), THRESHOLDS)
    }
    assert none["accepted"].status is GateStatus.NO_DATA


def test_missed_ticks_over_limit_fail() -> None:
    lines = {line.name: line for line in evaluate_gates(metrics(ticks_missed=3), THRESHOLDS)}
    assert lines["worker"].status is GateStatus.FAIL


def test_unconfirmed_thresholds_never_pass_the_gate() -> None:
    unconfirmed = GateThresholds("gates-1", 1000, 6000, 2, confirmed_by_owner=False)
    lines = evaluate_gates(metrics(), unconfirmed)
    assert all(line.status is not GateStatus.OK for line in lines if line.name != "worker")


def test_divergence_compares_per_ref_and_day() -> None:
    working = {("ad:1", "2026-11-10"): "pause_ad", ("ad:2", "2026-11-10"): "wait"}
    shadow = {
        ("ad:1", "2026-11-10"): "wait",
        ("ad:2", "2026-11-10"): "wait",
        ("ad:3", "2026-11-10"): "pause_ad",
    }
    assert count_divergence(working, shadow) == (2, 1)


def test_sample_is_deterministic_by_seed_and_bounded() -> None:
    ids = list(range(1, 51))
    first = pick_sample(ids, "2026-11-09", 10)
    assert first == pick_sample(list(reversed(ids)), "2026-11-09", 10)
    assert len(set(first)) == 10 and set(first) <= set(ids)
    assert first != pick_sample(ids, "2026-11-16", 10)
    assert sorted(pick_sample([5, 6], "s", 10)) == [5, 6]


def test_week_seed_is_monday_of_the_week_in_moscow() -> None:
    assert week_seed(datetime(2026, 11, 15, 22, 30, tzinfo=UTC)) == "2026-11-16"
    assert week_seed(datetime(2026, 11, 11, 10, 0, tzinfo=UTC)) == "2026-11-09"


def test_negative_thresholds_are_rejected() -> None:
    with pytest.raises(ValueError):
        GateThresholds("gates-x", -1, 6000, 2, False)
