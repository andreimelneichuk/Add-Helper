"""Логика A/B"""

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adhelper.experiments.adapters.rules_file import load_rules
from adhelper.experiments.domain.model import (
    IllegalTransition,
    Outcome,
    Status,
    VariantStats,
    evaluate,
    probability_b_better,
    transition,
)

RULES = load_rules("experiments-2")
SWITCHBACK = load_rules("experiments-1")


def stats(clicks: int, successes: int, spend: int = 1_000_000_000) -> VariantStats:
    return VariantStats(clicks, successes, spend)


def test_native_split_decides_at_80_percent_from_50_clicks_per_variant() -> None:
    """experiments-2: варианты идут одновременно, поэтому порог ниже, чем у чередования."""
    a, b = stats(60, 3), stats(60, 6)  # P(B лучше A) = 83,9%: округляем вниз.

    native = evaluate(a, b, 7, 0, RULES)
    assert native.outcome is Outcome.WINNER
    assert native.winner == "B"
    assert "83%" in native.explanation
    assert evaluate(a, b, 7, 0, SWITCHBACK).outcome is Outcome.CONTINUE
    assert evaluate(stats(49, 0), stats(49, 9), 7, 0, RULES).outcome is Outcome.CONTINUE


@given(st.integers(0, 60), st.integers(0, 60), st.integers(0, 60), st.integers(0, 60))
def test_probability_is_symmetric(s_a: int, f_a: int, s_b: int, f_b: int) -> None:
    forward = probability_b_better(stats(s_a + f_a, s_a), stats(s_b + f_b, s_b))
    backward = probability_b_better(stats(s_b + f_b, s_b), stats(s_a + f_a, s_a))
    assert abs(forward + backward - 1.0) < 1e-9


def test_equal_variants_are_a_coin_flip() -> None:
    assert abs(probability_b_better(stats(100, 10), stats(100, 10)) - 0.5) < 1e-9


def test_probability_matches_monte_carlo() -> None:
    a, b = stats(200, 20), stats(200, 32)
    rng = random.Random(7)
    hits = sum(rng.betavariate(33, 169) > rng.betavariate(21, 181) for _ in range(100_000))
    assert abs(probability_b_better(a, b) - hits / 100_000) < 0.01


def test_clear_winner_after_minimum_time() -> None:
    result = evaluate(stats(400, 20), stats(400, 50), 8, 0, RULES)
    assert result.outcome is Outcome.WINNER
    assert result.winner == "B"
    assert result.probability_b_bp >= RULES.win_probability_bp
    assert "вероятностью" in result.explanation


def test_control_can_win() -> None:
    result = evaluate(stats(400, 50), stats(400, 20), 8, 0, RULES)
    assert result.outcome is Outcome.WINNER
    assert result.winner == "A"


def test_no_early_stop_even_with_big_difference() -> None:
    assert evaluate(stats(400, 20), stats(400, 50), 6, 0, RULES).outcome is Outcome.CONTINUE


def test_no_stop_below_minimum_clicks() -> None:
    assert evaluate(stats(10, 0), stats(10, 8), 8, 0, RULES).outcome is Outcome.CONTINUE


def test_time_limit_with_small_difference_keeps_cheaper() -> None:
    result = evaluate(stats(400, 30, 3_000_000_000), stats(400, 30, 2_000_000_000), 21, 0, RULES)
    assert result.outcome is Outcome.NO_DIFFERENCE
    assert result.winner == "B"
    assert "Разница мала" in result.explanation


def test_tie_keeps_control() -> None:
    result = evaluate(stats(400, 30), stats(400, 30), 21, 0, RULES)
    assert result.outcome is Outcome.NO_DIFFERENCE
    assert result.winner == "A"


def test_time_limit_without_data_keeps_control_and_says_so() -> None:
    result = evaluate(stats(20, 1), stats(20, 3), 21, 0, RULES)
    assert result.outcome is Outcome.LOW_DATA
    assert result.winner == "A"
    assert "Данных мало" in result.explanation


def test_spend_limit_ends_test() -> None:
    result = evaluate(stats(400, 30), stats(400, 31), 8, RULES.max_test_spend, RULES)
    assert result.outcome is not Outcome.CONTINUE


def test_running_estimate_is_not_presented_as_conclusion() -> None:
    result = evaluate(stats(400, 20), stats(400, 50), 3, 0, RULES)
    assert result.outcome is Outcome.CONTINUE
    assert result.explanation == ""


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (Status.REVIEW, Status.RUNNING),
        (Status.REVIEW, Status.CANCELLED),
        (Status.RUNNING, Status.PAUSED),
        (Status.PAUSED, Status.RUNNING),
        (Status.RUNNING, Status.APPLIED),
        (Status.PAUSED, Status.CANCELLED),
    ],
)
def test_allowed_transitions(start: Status, end: Status) -> None:
    assert transition(start, end) is end


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (Status.REVIEW, Status.APPLIED),
        (Status.APPLIED, Status.RUNNING),
        (Status.CANCELLED, Status.RUNNING),
        (Status.PAUSED, Status.APPLIED),
    ],
)
def test_forbidden_transitions(start: Status, end: Status) -> None:
    with pytest.raises(IllegalTransition):
        transition(start, end)
