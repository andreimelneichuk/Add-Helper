"""Допустимая цена обращения из экономики бизнеса."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adhelper.business.adapters.rules_file import load_economics_rules
from adhelper.business.domain.economics import (
    Economics,
    EconomicsError,
    goal_micros,
    reference,
    validated,
)

RULES = load_economics_rules("economics-1")
MICROS = 1_000_000


def economics(**changes: object) -> Economics:
    base = Economics(
        lead_to_client_bp=3000,
        margin_bp=5000,
        repeat_bp=3000,
        ad_share_bp=3000,
        capacity_per_week=None,
        confirmed=False,
    )
    from dataclasses import replace

    return replace(base, **changes)  # type: ignore[arg-type]


def test_goal_is_check_times_margin_times_conversion_times_repeat_times_share() -> None:
    # 3000 ₽ × 50% × 30% × 1,3 × 30% = 175,5 ₽.
    assert goal_micros(3000 * MICROS, economics()) == 175_500_000


def test_goal_rounds_down_in_whole_micros_never_up() -> None:
    value = goal_micros(1_000_001, economics(lead_to_client_bp=3333, margin_bp=3333))
    assert isinstance(value, int)
    assert value == (1_000_001 * 3333 * 3333 * 13000 * 3000) // 10_000**4


def test_goal_without_a_check_does_not_exist() -> None:
    assert goal_micros(None, economics()) is None
    assert goal_micros(0, economics()) is None


@given(
    check=st.integers(min_value=1, max_value=10**12),
    margin=st.integers(min_value=1, max_value=10_000),
    lead=st.integers(min_value=1, max_value=10_000),
    repeat=st.integers(min_value=0, max_value=30_000),
    share=st.integers(min_value=1, max_value=10_000),
)
def test_goal_never_exceeds_margin_of_the_check_scaled_by_repeat(
    check: int, margin: int, lead: int, repeat: int, share: int
) -> None:
    e = economics(margin_bp=margin, lead_to_client_bp=lead, repeat_bp=repeat, ad_share_bp=share)
    value = goal_micros(check, e)
    assert value is not None and value >= 0
    # Доли не больше целого, повторные не больше четырёх визитов: цена не больше 4 чеков.
    assert value <= check * 4


@given(
    low=st.integers(min_value=1, max_value=9_999),
    step=st.integers(min_value=1, max_value=1_000),
)
def test_goal_grows_with_every_share(low: int, step: int) -> None:
    high = min(low + step, 10_000)
    check = 5_000 * MICROS
    for name in ("margin_bp", "lead_to_client_bp", "ad_share_bp"):
        a = goal_micros(check, economics(**{name: low}))
        b = goal_micros(check, economics(**{name: high}))
        assert a is not None and b is not None and a <= b


@pytest.mark.parametrize(
    "changes",
    [
        {"margin_bp": 0},
        {"margin_bp": -1},
        {"margin_bp": 10_001},
        {"lead_to_client_bp": 0},
        {"ad_share_bp": 0},
        {"ad_share_bp": 10_001},
        {"repeat_bp": -1},
        {"repeat_bp": 30_001},
        {"capacity_per_week": 0},
        {"capacity_per_week": -5},
    ],
)
def test_out_of_range_values_are_rejected(changes: dict[str, int]) -> None:
    with pytest.raises(EconomicsError):
        validated(economics(**changes), RULES)


def test_values_inside_the_rules_are_accepted() -> None:
    e = economics(capacity_per_week=20)
    assert validated(e, RULES) == e


def test_reference_values_come_from_the_rules_and_are_not_confirmed() -> None:
    ref = reference(RULES)
    assert ref.confirmed is False
    assert 0 < ref.margin_bp <= 10_000 and 0 < ref.ad_share_bp <= 10_000
    assert ref.capacity_per_week is None
