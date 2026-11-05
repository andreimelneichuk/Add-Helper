"""Карточки «Сегодня»: docs/10-redesign.md §4 «Сегодня», порядок по правилам, числа от кода."""

from datetime import UTC, datetime, timedelta

from adhelper.stats.adapters.rules_file import load_today_rules
from adhelper.stats.domain.model import AlertLine, HistoryLine, TestLine, TestOffer, TestResult
from adhelper.stats.domain.today import TodayFacts, cards

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
RULES = load_today_rules("today-1")
RULES_2 = load_today_rules("today-2")
RULES_3 = load_today_rules("today-3")
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


def test_rejected_ad_names_headline_and_reason_from_alert() -> None:
    details = {"count": 2, "headline": "Маникюр у дома", "reason": "Яндекс не смог открыть сайт"}
    shown = cards(facts(alerts=(AlertLine("ads_rejected", details, NOW),)), RULES)[0]
    assert shown.text.startswith("«Маникюр у дома» и ещё 1 не прошли проверку Яндекса.")
    assert "Яндекс не смог открыть сайт." in shown.text
    one = cards(facts(alerts=(AlertLine("ads_rejected", {**details, "count": 1}, NOW),)), RULES)
    assert one[0].text.startswith("«Маникюр у дома» не прошло проверку Яндекса.")
    bare = cards(facts(alerts=(alert("ads_rejected"),)), RULES)[0]
    assert bare.text.startswith("Остальные объявления работают.")


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


def test_owner_action_text_does_not_repeat_itself() -> None:
    # Объяснение просьбы владельца уже говорит «Вы попросили» и как отменить: не повторяем.
    told = "Показываю рекламу в регионе: Санкт-Петербург. Вы попросили. Отменить можно в истории."
    shown = cards(facts(history=(change("set_regions", "user", explanation=told),)), RULES)
    assert shown[1].text == told
    plain_line = change("pause_ad", "user", explanation="Отключил объявление.")
    shown = cards(facts(history=(plain_line,)), RULES)
    assert shown[1].text == "Вы попросили. Отключил объявление. Отменить можно в истории."


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


OFFER = TestOffer(41, "Маникюр с покрытием", "price", 1200 * RUB, 3, 300 * RUB, 400 * RUB)


def test_test_offer_names_numbers_hypothesis_and_how_to_refuse() -> None:
    shown = cards(facts(offers=(OFFER,)), RULES_2)

    offer = next(c for c in shown if c.kind == "test_offer")
    assert offer.action == "test_offer" and offer.ref == "41"
    text = plain(offer.text)
    assert "«Маникюр с покрытием» потратило 1 200 ₽ и принесло 3 обращения" in text
    assert "по 400 ₽ при цели 300 ₽" in text
    assert "с ценой из вашего профиля" in text
    assert "Бюджет не меняется" in text
    assert offer.rules == "today-2"


def test_offer_without_leads_does_not_divide_by_zero() -> None:
    quiet = TestOffer(5, "Педикюр", "call_to_action", 900 * RUB, 0, 300 * RUB, 400 * RUB)
    offer = next(c for c in cards(facts(offers=(quiet,)), RULES_2) if c.kind == "test_offer")
    assert "обращений пока нет" in plain(offer.text)


def test_preparing_test_says_what_waits_and_offers_cancel() -> None:
    line = TestLine(9, "Маникюр с покрытием", "benefit", "review")
    card = next(c for c in cards(facts(tests=(line,)), RULES_2) if c.kind == "test_preparing")
    assert card.action == "test_cancel" and card.ref == "9"
    assert "доступ к API Директа" in plain(card.text)
    assert "работает как раньше" in plain(card.text)


def test_running_test_shows_no_early_verdict() -> None:
    line = TestLine(9, "Маникюр с покрытием", "benefit", "running")
    card = next(c for c in cards(facts(tests=(line,)), RULES_2) if c.kind == "test_running")
    assert card.action is None
    assert "Промежуточные цифры" in plain(card.text)


def test_offer_does_not_hide_a_problem() -> None:
    shown = cards(facts(alerts=(alert("zero_balance"),), offers=(OFFER,)), RULES_2)
    assert [c.kind for c in shown][:1] == ["alert:zero_balance"]


def result(winner: str = "B", days_ago: int = 1) -> TestResult:
    return TestResult(
        id=9,
        control_title="Маникюр в «Лак»",
        variant_title="Маникюр за 1500 ₽",
        hypothesis="price",
        winner=winner,
        explanation="С вероятностью 83% новый вариант лучше, его и оставили.",
        finished_at=NOW - timedelta(days=days_ago),
    )


def test_finished_test_shows_winner_and_paused_loser_with_way_back() -> None:
    card = next(c for c in cards(facts(results=(result(),)), RULES_3) if c.kind == "test_result")
    text = plain(card.text)
    assert card.title == "A/B-тест закончен: новый вариант лучше"
    assert card.tone == "good"
    assert "С вероятностью 83%" in text
    assert "«Маникюр за 1500 ₽» остаётся" in text
    assert "«Маникюр в «Лак»» приостановлено" in text
    assert "в истории" in text
    assert (card.action, card.ref, card.rules) == ("history", "experiment-9", "today-3")


def test_control_kept_is_said_plainly() -> None:
    kept = result(winner="A")
    card = next(c for c in cards(facts(results=(kept,)), RULES_3) if c.kind == "test_result")
    assert card.title == "A/B-тест закончен: прежнее объявление остаётся"
    assert card.tone == "info"
    assert "«Маникюр за 1500 ₽» приостановлено" in plain(card.text)


def test_old_result_leaves_today() -> None:
    old = result(days_ago=RULES_3.last_action_days + 1)
    assert "test_result" not in [c.kind for c in cards(facts(results=(old,)), RULES_3)]


def test_offer_says_variants_run_together() -> None:
    offer = next(c for c in cards(facts(offers=(OFFER,)), RULES_3) if c.kind == "test_offer")
    assert "одновременно" in plain(offer.text)
    assert "чередоваться" not in plain(offer.text)
