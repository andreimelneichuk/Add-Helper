"""Карточки «Сегодня»: docs/10-redesign.md §4 «Сегодня», порядок по правилам, числа от кода."""

from datetime import UTC, datetime, timedelta

from adhelper.stats.adapters.rules_file import load_today_rules
from adhelper.stats.domain.model import AlertLine, HistoryLine
from adhelper.stats.domain.today import TodayFacts, cards

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
RULES = load_today_rules("today-1")
RUB = 1_000_000


def plain(text: str) -> str:
    return text.replace("\u00a0", " ")


def alert(kind: str) -> AlertLine:
    return AlertLine(kind, {}, NOW)


def change(kind: str = "pause_ad", source: str = "autopilot", **extra: object) -> HistoryLine:
    values: dict[str, object] = {
        "ref": "change-7",
        "at": NOW - timedelta(days=1),
        "source": source,
        "kind": kind,
        "target": "ad:11",
        "status": "applied",
        "explanation": "За 12 дней 1 840 ₽ и ни одного обращения.",
        "before": {"active": True},
        "after": {"active": False},
        "rules_version": None,
    } | extra
    return HistoryLine(**values)  # type: ignore[arg-type]


def facts(**extra: object) -> TodayFacts:
    values: dict[str, object] = {
        "now": NOW,
        "alerts": (),
        "balance_gap": None,
        "paused": False,
        "leads": 12,
        "cost": 5040 * RUB,
        "prev_leads": 9,
        "prev_cost": 4500 * RUB,
        "history": (),
    } | extra
    return TodayFacts(**values)  # type: ignore[arg-type]


def test_calm_week_is_one_card_with_numbers() -> None:
    shown = cards(facts(), RULES)
    assert [c.kind for c in shown] == ["summary"]
    assert shown[0].tone == "good"
    assert plain(shown[0].title) == "Всё в порядке: 12 обращений за 7 дней, по 420 ₽"
    assert "Неделей раньше было 9 по 500 ₽" in plain(shown[0].text)


def test_problem_goes_first_and_summary_stops_saying_all_is_fine() -> None:
    shown = cards(facts(alerts=(alert("budget_near_limit"), alert("zero_balance"))), RULES)
    assert [c.kind for c in shown] == ["alert:zero_balance", "alert:budget_near_limit", "summary"]
    assert shown[0].tone == "bad"
    assert not shown[-1].title.startswith("Всё в порядке")


def test_mismatch_names_the_gap_from_code() -> None:
    shown = cards(facts(alerts=(alert("balance_mismatch"),), balance_gap=1_200_500_000), RULES)
    assert "на 1 201 ₽ больше" in shown[0].text.replace(" ", " ")
    assert shown[0].action == "balance"


def test_owner_pause_card_offers_resume() -> None:
    shown = cards(facts(paused=True), RULES)
    assert shown[0].kind == "paused"
    assert shown[0].action == "resume"


def test_recent_undoable_action_is_shown_with_undo() -> None:
    shown = cards(facts(history=(change(),)), RULES)
    assert [c.kind for c in shown] == ["summary", "last_action"]
    assert shown[1].action == "undo"
    assert shown[1].ref == "change-7"
    assert "1 840 ₽" in plain(shown[1].text)


def test_old_reverted_or_foreign_actions_are_not_offered() -> None:
    old = change(at=NOW - timedelta(days=RULES.last_action_days + 1))
    for line in (old, change(reverted=True), change(source="external"), change(source="rollback")):
        assert [c.kind for c in cards(facts(history=(line,)), RULES)] == ["summary"]


def test_little_data_is_said_honestly() -> None:
    shown = cards(facts(leads=0, cost=700 * RUB), RULES)
    assert shown[0].title == "Обращений за 7 дней пока нет"
    assert "Данных пока мало" in shown[0].text
    nothing = cards(facts(leads=0, cost=0), RULES)
    assert nothing[0].title == "За 7 дней показов не было"


def test_feed_is_finite_and_unknown_alert_is_not_lost() -> None:
    many = tuple(
        alert(k) for k in ("zero_balance", "platform_unavailable", "x_new", "budget_stopped")
    )
    shown = cards(facts(alerts=many, history=(change(),)), RULES)
    assert len(shown) == RULES.max_cards
    assert "alert:x_new" in [c.kind for c in shown]
    assert next(c for c in shown if c.kind == "alert:x_new").title == "Нужна ваша проверка"


def test_every_card_carries_rules_version() -> None:
    assert {c.rules for c in cards(facts(paused=True), RULES)} == {"today-1"}
