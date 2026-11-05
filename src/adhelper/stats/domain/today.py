"""Карточки «Сегодня»: что важно сейчас, по порядку из правил. docs/10-redesign.md §4.

Набор и порядок решают правила, числа подставляет код. Модель здесь не участвует.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from adhelper.stats.domain.model import AlertLine, HistoryLine, TestLine, TestOffer, TestResult

MICROS_PER_RUB = 1_000_000
# Что владелец может отменить из истории: совпадает с откатом журнала.
UNDOABLE = frozenset(
    {"set_limit", "resume", "pause_ad", "resume_ad", "set_schedule", "set_regions"}
)
# Чьи действия напоминаем: свои и сервиса. Изменения из кабинета и сами отмены — нет.
ACTORS = frozenset({"autopilot", "user", "experiment"})
ASKED = "Вы попросили."
UNDO_HINT = "Отменить можно в истории."
ACTION_TITLE = {
    "set_limit": "Изменил дневной лимит",
    "resume": "Снял паузу",
    "pause_ad": "Отключил объявление",
    "resume_ad": "Включил объявление",
    "set_schedule": "Изменил время показов",
    "set_regions": "Изменил регион показов",
}


@dataclass(frozen=True)
class TodayRules:
    version: str
    max_cards: int
    last_action_days: int
    unknown_alert: int
    priority: Mapping[str, int]


@dataclass(frozen=True)
class TodayFacts:
    """Всё, из чего собирается лента. Деньги в микроединицах площадки."""

    now: datetime
    alerts: Sequence[AlertLine]
    balance_gap: int | None
    paused: bool
    leads: int
    cost: int
    prev_leads: int
    prev_cost: int
    history: Sequence[HistoryLine]
    offers: Sequence[TestOffer] = ()
    tests: Sequence[TestLine] = ()
    results: Sequence[TestResult] = ()


@dataclass(frozen=True)
class Card:
    kind: str
    tone: str
    title: str
    text: str
    # Действие из закрытого набора, экран сам знает, что по нему открыть.
    action: str | None
    ref: str | None
    rules: str


@dataclass(frozen=True)
class AlertWords:
    title: str
    tone: str
    text: str
    action: str | None = None


ALERTS = {
    "balance_mismatch": AlertWords(
        "Счёт не сходится со статистикой",
        "bad",
        "Показы остановлены, пока не ясно, куда уходят деньги.",
        "balance",
    ),
    "zero_balance": AlertWords(
        "На счёте закончились деньги",
        "bad",
        "Пополните счёт в кабинете Яндекса. Показы стоят, пока денег нет.",
    ),
    "budget_stopped": AlertWords(
        "Бюджет месяца исчерпан, показы остановлены",
        "warn",
        "Перерасхода не будет. Показы включатся в новом месяце.",
    ),
    "budget_stop_pending": AlertWords(
        "Не удалось остановить показы",
        "bad",
        "Яндекс пока не принял остановку, продолжаю пробовать. Лимиты стоят в самом кабинете.",
    ),
    "budget_near_limit": AlertWords(
        "Бюджет месяца почти израсходован",
        "warn",
        "Когда бюджет закончится, показы остановятся сами. Перерасхода не будет.",
    ),
    "platform_unavailable": AlertWords(
        "Яндекс Директ не отвечает",
        "warn",
        "Пока Яндекс не отвечает, я ничего не меняю. Лимиты стоят в самом кабинете.",
    ),
    "platform_not_connected": AlertWords(
        "Кабинет Яндекс Директа не подключён",
        "warn",
        "Пока кабинет не подключён, изменения не дойдут до Яндекса."
        " Подключить его можно в настройках.",
        "settings",
    ),
    "ads_no_profile": AlertWords(
        "Не о чем писать объявления: добавьте услуги в профиль",
        "warn",
        "Добавьте услуги в профиль в настройках: в рекламу попадают только факты из него.",
        "settings",
    ),
    "ads_need_confirmation": AlertWords(
        "Новые объявления ждут вашего решения",
        "warn",
        "Посмотрите их в разделе «Реклама» и решите, запускать ли.",
        "ads",
    ),
    "ads_rejected": AlertWords(
        "Яндекс отклонил объявление, переписать не вышло",
        "warn",
        "Остальные объявления работают. Откройте отклонённое в разделе «Реклама»: попросите"
        " переделать своими словами или отключите его.",
        "ads",
    ),
}
UNKNOWN_ALERT = AlertWords("Нужна ваша проверка", "warn", "Подробности в истории.", "history")


def rub(micros: int) -> str:
    """Округление только при показе: до целого рубля."""
    whole = (micros + MICROS_PER_RUB // 2) // MICROS_PER_RUB
    return f"{whole:,}".replace(",", " ") + " ₽"


def leads_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "обращение"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "обращения"
    return "обращений"


def _rejected(details: Mapping[str, Any]) -> str:
    """Какое объявление и почему: заголовок из нашей строки, причина — текст правил, не площадки."""
    headline = details.get("headline")
    if not isinstance(headline, str) or not headline:
        return ""
    count = details.get("count")
    more = count - 1 if isinstance(count, int) and count > 1 else 0
    said = f"«{headline}» и ещё {more} не прошли" if more else f"«{headline}» не прошло"
    reason = details.get("reason")
    why = f" {reason}." if isinstance(reason, str) and reason else ""
    return f"{said} проверку Яндекса.{why} "


def _alert(line: AlertLine, gap: int | None, rules: TodayRules) -> tuple[int, Card]:
    words = ALERTS.get(line.kind, UNKNOWN_ALERT)
    text = words.text
    if line.kind == "balance_mismatch" and gap is not None and gap > 0:
        text = (
            f"Со счёта ушло на {rub(gap)} больше, чем реклама потратила по статистике Яндекса. "
            + text
        )
    if line.kind == "ads_rejected":
        text = _rejected(line.details) + text
    kind = f"alert:{line.kind}"
    place = rules.priority.get(kind, rules.unknown_alert)
    return place, Card(kind, words.tone, words.title, text, words.action, None, rules.version)


def _summary(facts: TodayFacts, calm: bool, rules: TodayRules) -> Card:
    if facts.leads == 0:
        if facts.cost > 0:
            title = "Обращений за 7 дней пока нет"
            text = f"Потрачено {rub(facts.cost)}. Данных пока мало для выводов."
        else:
            title = "За 7 дней показов не было"
            text = "Данных пока нет, поэтому выводов тоже нет."
        return Card("summary", "info", title, text, None, None, rules.version)
    each = rub(facts.cost // facts.leads)
    numbers = f"{facts.leads} {leads_word(facts.leads)} за 7 дней, по {each}"
    was = (
        f"Неделей раньше было {facts.prev_leads} по {rub(facts.prev_cost // facts.prev_leads)}. "
        if facts.prev_leads > 0
        else ""
    )
    if calm:
        return Card(
            "summary",
            "good",
            f"Всё в порядке: {numbers}",
            f"{was}Вмешиваться не нужно.",
            None,
            None,
            rules.version,
        )
    return Card(
        "summary",
        "info",
        numbers[0].upper() + numbers[1:],
        was.strip(),
        None,
        None,
        rules.version,
    )


def _last_action(facts: TodayFacts, rules: TodayRules) -> Card | None:
    since = facts.now - timedelta(days=rules.last_action_days)
    for line in facts.history:
        if not line.ref.startswith("change-") or line.at < since:
            continue
        if line.source not in ACTORS or line.kind not in UNDOABLE:
            continue
        if line.status != "applied" or line.reverted:
            continue
        # Часть объяснений уже говорит, кто попросил и как отменить: не повторяем.
        who = "" if line.source != "user" or ASKED in line.explanation else f"{ASKED} "
        tail = "" if UNDO_HINT in line.explanation else f" {UNDO_HINT}"
        return Card(
            "last_action",
            "info",
            ACTION_TITLE[line.kind],
            f"{who}{line.explanation}{tail}",
            "undo",
            line.ref,
            rules.version,
        )
    return None


# Что проверит новый вариант. Только гипотезы, которые подтверждает профиль (docs/04 §5).
HYPOTHESIS = {
    "price": "с ценой из вашего профиля",
    "benefit": "с вашим преимуществом из профиля",
    "call_to_action": "с другим призывом к действию",
}
WAITS_FOR_API = (
    "Новый вариант напишу и отправлю в Яндекс, когда сервис получит доступ к API Директа."
    " До тех пор объявление работает как раньше."
)


def _offer(offer: TestOffer, rules: TodayRules) -> Card:
    spent = f"«{offer.ad_title}» потратило {rub(offer.spend)}"
    if offer.leads > 0:
        result = (
            f"{spent} и принесло {offer.leads} {leads_word(offer.leads)},"
            f" по {rub(offer.spend // offer.leads)} при цели {rub(offer.target)}."
        )
    else:
        result = f"{spent}, обращений пока нет. Цель — {rub(offer.target)} за обращение."
    return Card(
        "test_offer",
        "info",
        "Предлагаю проверить новый вариант объявления",
        f"{result} Плохим его назвать нельзя, хорошим тоже, а данных для вывода мало."
        f" Предлагаю A/B-тест: рядом встанет вариант {HYPOTHESIS.get(offer.hypothesis, '')},"
        " оба будут показываться одновременно от 7 до 21 дня, Яндекс сам поделит показы."
        " Бюджет не меняется."
        " Отказаться можно сейчас, отменить — пока тест готовится.",
        "test_offer",
        str(offer.decision_id),
        rules.version,
    )


def _test(line: TestLine, rules: TodayRules) -> Card | None:
    what = f"«{line.ad_title}» против варианта {HYPOTHESIS.get(line.hypothesis, '')}."
    if line.status == "review":
        return Card(
            "test_preparing",
            "info",
            "Готовлю A/B-тест",
            f"{what} {WAITS_FOR_API} Другие тесты на этом направлении пока не начнутся.",
            "test_cancel",
            str(line.id),
            rules.version,
        )
    if line.status in ("running", "paused"):
        return Card(
            "test_running",
            "info",
            "Идёт A/B-тест" if line.status == "running" else "A/B-тест приостановлен",
            f"{what} Итог покажу, когда данных хватит для вывода."
            " Промежуточные цифры выводом не считаются.",
            None,
            str(line.id),
            rules.version,
        )
    return None


def _result(line: TestResult, rules: TodayRules) -> Card:
    """Итог теста: что решено и почему, с цифрой из анализа, и как вернуть проигравшего."""
    new_won = line.winner == "B"
    kept, paused = (
        (line.variant_title, line.control_title)
        if new_won
        else (line.control_title, line.variant_title)
    )
    title = "новый вариант лучше" if new_won else "прежнее объявление остаётся"
    return Card(
        "test_result",
        "good" if new_won else "info",
        f"A/B-тест закончен: {title}",
        f"{line.explanation} «{kept}» остаётся, «{paused}» приостановлено, а не удалено."
        " Включить его снова можно в истории.",
        "history",
        f"experiment-{line.id}",
        rules.version,
    )


def cards(facts: TodayFacts, rules: TodayRules) -> list[Card]:
    """Лента «Сегодня»: сначала то, что требует внимания; если всё хорошо — одна строка."""
    placed: list[tuple[int, Card]] = [_alert(a, facts.balance_gap, rules) for a in facts.alerts]
    if facts.paused:
        placed.append(
            (
                rules.priority["paused"],
                Card(
                    "paused",
                    "bad",
                    "Реклама на паузе",
                    "Вы поставили паузу. Настройки сохранены, деньги не тратятся."
                    " Перед включением проверю, что расход не выйдет за бюджет.",
                    "resume",
                    None,
                    rules.version,
                ),
            )
        )
    calm = not placed
    placed.append((rules.priority["summary"], _summary(facts, calm, rules)))
    for offer in facts.offers:
        placed.append((rules.priority["test_offer"], _offer(offer, rules)))
    for line in facts.tests:
        card = _test(line, rules)
        if card is not None:
            placed.append((rules.priority[card.kind], card))
    since = facts.now - timedelta(days=rules.last_action_days)
    for result in facts.results:
        if "test_result" in rules.priority and result.finished_at >= since:
            placed.append((rules.priority["test_result"], _result(result, rules)))
    last = _last_action(facts, rules)
    if last is not None:
        placed.append((rules.priority["last_action"], last))
    # Устойчивая сортировка: при равном месте порядок тревог как у сервиса.
    ordered = sorted(placed, key=lambda item: item[0])
    return [card for _, card in ordered[: rules.max_cards]]
