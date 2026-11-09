"""Тексты автопилота для владельца: числа подставляет код, в сводке нет выдуманного."""

from datetime import UTC, datetime

from adhelper.notifications.domain.messages import compose

AT = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def text(kind: str, details: dict[str, object]) -> str:
    return compose(kind, "Салон", AT, None, details, "https://example.test").text


def test_digest_counts_come_from_details() -> None:
    body = text(
        "autopilot_digest", {"done": 3, "better": 1, "same": 2, "worse": 1, "rolled_back": 1}
    )
    assert "Действий за сутки: 3." in body
    assert "лучше стало в 1, без заметной разницы в 2, хуже в 1" in body
    assert "Отменили сами: 1." in body


def test_digest_without_data_says_so() -> None:
    body = text("autopilot_digest", {})
    assert "Данных пока мало" in body


def test_demoted_names_the_reason_and_what_stays() -> None:
    body = text("autopilot_demoted", {"reason": "за сутки уже 2 автоматических отката"})
    assert "за сутки уже 2 автоматических отката" in body
    assert "«Предлагает»" in body


def test_rolled_back_uses_the_prepared_explanation() -> None:
    body = text("autopilot_rolled_back", {"explanation": "Вернули объявление в показ."})
    assert "Вернули объявление в показ." in body
    assert "Бизнес: Салон." in body and "С " not in body.split("Бизнес: Салон.")[1][:3]
