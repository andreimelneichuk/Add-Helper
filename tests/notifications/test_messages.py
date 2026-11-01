"""Тексты тревог: цифры подставляет код, без цифр текст прямо говорит об этом."""

from datetime import UTC, datetime

import pytest

from adhelper.notifications.domain.messages import NO_NUMBERS, TITLES, compose
from adhelper.notifications.domain.model import MoneyFacts, next_attempt

M = 1_000_000
AT = datetime(2026, 11, 1, 10, 4, tzinfo=UTC)
MONEY = MoneyFacts(
    hard_limit=40_000 * M,
    spendable=36_000 * M,
    tolerance=800 * M,
    spent=9_000 * M,
    last_balance=57_603 * M,
    balance_gap=1_240 * M,
)
URL = "https://adhelper.example"


def test_mismatch_explains_numbers_action_and_way_back() -> None:
    message = compose("balance_mismatch", "Студия «Лак»", AT, MONEY, {}, URL)
    assert message.subject == f"{TITLES['balance_mismatch']} — Студия «Лак»"
    assert "1 240 ₽" in message.text and "800 ₽" in message.text
    assert "остановили показы" in message.text
    assert "подтвердите" in message.text
    assert "01.11.2026 13:04 по Москве" in message.text
    assert message.text.endswith(f"Кабинет: {URL}")


def test_without_numbers_says_so_instead_of_inventing() -> None:
    message = compose("balance_mismatch", "Лак", AT, None, {}, URL)
    assert NO_NUMBERS in message.text
    assert "₽" not in message.text


def test_near_limit_uses_projection_from_alert() -> None:
    message = compose(
        "budget_near_limit", "Лак", AT, MONEY, {"projected_micros": 38_999_600_000}, URL
    )
    assert "39 000 ₽ из 40 000 ₽" in message.text


@pytest.mark.parametrize("kind", [*TITLES, "something_new"])
def test_every_kind_has_text_and_link(kind: str) -> None:
    for money in (MONEY, None):
        message = compose(kind, "Лак", AT, money, {}, URL)
        assert "\n" not in message.subject
        assert URL in message.text
        assert len(message.text) < 1500


def test_retries_grow_then_stop() -> None:
    delays = [next_attempt(n, AT) for n in range(1, 7)]
    assert all(d is not None for d in delays[:5])
    assert [d - AT for d in delays[:5] if d] == sorted(d - AT for d in delays[:5] if d)
    assert delays[5] is None
