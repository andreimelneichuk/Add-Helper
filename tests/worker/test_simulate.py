"""`adhelper simulate`: две версии правил на истории кабинета. docs/04-autopilot.md §4."""

import uuid
from datetime import date

import pytest

from adhelper.autopilot.application.simulator import Difference
from adhelper.config import Settings
from adhelper.worker.cli import describe_difference, history_report, main
from tests.autopilot.test_data import (  # noqa: F401  — тот же набор данных и фикстура
    BUSINESS,
    Sessions,
    _seed,
    run,
)


async def goal(sessions: Sessions, rubles: int) -> None:
    await run(
        sessions,
        "INSERT INTO lead_goals (business_id, goal_micros) VALUES (:b, :g)",
        b=BUSINESS,
        g=rubles * 1_000_000,
    )


async def test_same_rules_report_no_changes(sessions: Sessions) -> None:
    await goal(sessions, 300)

    lines = await history_report(sessions, BUSINESS, "autopilot-2", "autopilot-2", None)

    assert lines[0].startswith("Кабинет acc: 30 дней")
    assert "цель 300 ₽" in lines[0]
    assert "autopilot-2 → autopilot-2" in lines[0]
    assert lines[-1] == "Решения не изменились."


async def test_target_from_command_line_overrides_goal(sessions: Sessions) -> None:
    lines = await history_report(sessions, BUSINESS, "autopilot-1", "autopilot-2", 500)
    assert "цель 500 ₽" in lines[0]


async def test_without_goal_says_what_to_do(sessions: Sessions) -> None:
    lines = await history_report(sessions, BUSINESS, "autopilot-1", "autopilot-2", None)
    assert lines == ["Нет цели цены обращения: задайте её в кабинете или через --target."]


async def test_business_without_history(sessions: Sessions) -> None:
    lines = await history_report(sessions, uuid.uuid4(), "autopilot-1", "autopilot-2", 300)
    assert lines == ["У бизнеса нет подключённых кабинетов с историей."]


def test_difference_is_named_in_words() -> None:
    line = describe_difference(Difference(date(2026, 10, 12), "ad:1012", "start_test", None))
    assert line == "2026-10-12 ad:1012: тест → ничего"
    line = describe_difference(Difference(date(2026, 10, 13), "account", None, "protect_only"))
    assert line == "2026-10-13 account: ничего → только защита"


def test_unknown_rules_version_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["simulate", "--business", str(uuid.uuid4()), "--against", "autopilot-99"])
    assert code == 2
    assert "autopilot-99" in capsys.readouterr().out


def test_unreachable_database_does_not_leak_password(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("POSTGRES_PORT", "1")
    code = main(["simulate", "--business", str(uuid.uuid4())])
    out = capsys.readouterr().out
    assert code == 1
    assert settings.postgres_password.get_secret_value() not in out


def test_command_runs_on_recorded_history(
    sessions: Sessions, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["simulate", "--business", str(BUSINESS), "--target", "300"])
    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert out[0].startswith("Кабинет acc: 30 дней")
    assert out[-1] == "Решения не изменились." or out[-1].startswith("Расхождений:")


def _shadow(**inputs: object):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from adhelper.autopilot.adapters.sql import LoggedDecision

    return LoggedDecision(
        1,
        "shadow:ad:7",
        "inefficient",
        inputs.get("action"),  # type: ignore[arg-type]
        "shadow",
        inputs,
        "autopilot-3",
        datetime(2026, 11, 20, 9, 0, tzinfo=UTC),
        "acc",
    )


def test_shadow_line_says_what_the_rules_would_do_with_numbers() -> None:
    from adhelper.worker.cli import describe_shadow

    line = describe_shadow(
        _shadow(
            action="pause_ad",
            would="pause_ad",
            reason="none",
            p_worse_bp=9681,
            loss_micros=2_876_840_810,
            error_cost_micros=900_000_000,
            clicks=400,
            leads=10,
            better_ref="ad:3",
        )
    )
    assert line == (
        "2026-11-20 ad:7: сделала бы паузу объявления; хуже ad:3 с вероятностью 96%,"
        " недобор за неделю 2876 ₽ против цены ошибки 900 ₽ (400 кликов, 10 обращений)"
    )


def test_shadow_line_for_waiting_names_the_reason_in_words() -> None:
    from adhelper.worker.cli import describe_shadow

    line = describe_shadow(_shadow(would="wait", reason="low_data", clicks=100, leads=3))
    assert line == "2026-11-20 ad:7: ждала бы: данных мало (100 кликов, 3 обращения)"
    line = describe_shadow(_shadow(would="protect_only", reasons=["platform_incident"]))
    assert line.endswith("только защита: сбой площадки")


async def test_shadow_report_without_rows_says_so(sessions: Sessions) -> None:
    from adhelper.worker.cli import shadow_report

    lines = await shadow_report(sessions, BUSINESS, 50)
    assert lines == ["Теневых записей пока нет: правила autopilot-3 ещё не считали этот кабинет."]
    assert await shadow_report(sessions, uuid.uuid4(), 50) == [
        "У бизнеса нет подключённых кабинетов."
    ]
