"""Тексты тревог для владельца. Числа подставляет код; модель тексты не пишет.

Каждое сообщение отвечает: что случилось, что мы сделали, цифры, что дальше, где отменить.
Чего нет в данных, того нет и в тексте: без цифр сообщение говорит об этом прямо.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from adhelper.notifications.domain.model import NOTICES, Message, MoneyFacts

PLATFORM_ZONE = ZoneInfo("Europe/Moscow")
NO_NUMBERS = "Свежих цифр по деньгам у нас нет: подробности в кабинете."

TITLES = {
    "balance_mismatch": "Счёт не сходится со статистикой, показы остановлены",
    "zero_balance": "На счёте Директа закончились деньги",
    "budget_stopped": "Бюджет месяца исчерпан, показы остановлены",
    "budget_stop_pending": "Не удалось остановить показы",
    "budget_near_limit": "Бюджет месяца почти израсходован",
    "platform_unavailable": "Яндекс Директ не отвечает",
    "platform_not_connected": "Кабинет Директа не подключён",
    "ads_no_profile": "Не о чем писать объявления",
    "ads_need_confirmation": "Новые объявления ждут вашего решения",
    "ads_rejected": "Яндекс отклонил объявление",
    "ads_launched": "Запущены новые объявления",
}
SUMMARY_SHOWN = 5


def rubles(micros: int) -> str:
    """Округление только при показе."""
    whole = (micros + 500_000) // 1_000_000
    return f"{whole:,}".replace(",", " ") + " ₽"


def _moment(at: datetime) -> str:
    local = at.astimezone(PLATFORM_ZONE)
    return f"{local:%d.%m.%Y %H:%M} по Москве"


def _ads(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} объявление"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"{count} объявления"
    return f"{count} объявлений"


def _launched(details: dict[str, object]) -> list[str]:
    raw = details.get("headlines")
    headlines = [h for h in raw if isinstance(h, str)] if isinstance(raw, list) else []
    count = details.get("count")
    total = count if isinstance(count, int) and count >= len(headlines) else len(headlines)
    day = details.get("day")
    when = ""
    if isinstance(day, str):
        try:
            when = f" {date.fromisoformat(day):%d.%m.%Y}"
        except ValueError:
            when = ""
    lines = [f"Яндекс одобрил{when}, и мы запустили {_ads(total)}:"]
    lines += [f"— «{headline}»" for headline in headlines[:SUMMARY_SHOWN]]
    if total > SUMMARY_SHOWN:
        lines.append(f"И ещё {_ads(total - SUMMARY_SHOWN)}.")
    lines += [
        "Все факты в них — из вашего профиля. Мы сравним их с прежними и оставим лучшие.",
        "Если что-то не так, снимите объявление одним нажатием в разделе «Реклама»:"
        " отменить снятие можно в истории.",
    ]
    return lines


def _body(kind: str, money: MoneyFacts | None, details: dict[str, object]) -> list[str]:
    if kind == "balance_mismatch":
        if money is None or money.balance_gap is None:
            facts = "Со счёта ушло заметно больше, чем реклама потратила по статистике Яндекса."
            numbers = [facts, NO_NUMBERS]
        else:
            numbers = [
                f"Со счёта ушло на {rubles(money.balance_gap)} больше, чем реклама потратила "
                f"по статистике Яндекса. Допуск — {rubles(money.tolerance)}."
            ]
        return [
            *numbers,
            "Мы остановили показы: пока не ясно, куда уходят деньги, тратить их дальше нельзя.",
            "Так бывает, если в кабинете идёт реклама не через Ad Helper или деньги переводили "
            "в другой кабинет. Если причина вам известна, подтвердите это в кабинете: сверка "
            "начнётся заново. Показы после этого включаются отдельно.",
        ]
    if kind == "zero_balance":
        balance = money.last_balance if money else None
        shown = f"На счёте {rubles(max(balance, 0))}. " if balance is not None else ""
        return [
            f"{shown}Яндекс остановил показы, пока счёт не пополнят. Мы ничего не меняли.",
            "Пополните счёт в Директе: показы возобновятся сами.",
        ]
    if kind == "budget_stopped":
        lines = (
            [NO_NUMBERS]
            if money is None or money.spent is None
            else [f"Потрачено {rubles(money.spent)} из {rubles(money.hard_limit)} бюджета месяца."]
        )
        return [
            *lines,
            "Чтобы не выйти за бюджет, мы остановили показы до конца периода.",
            "Новый период начнётся после того, как вы подтвердите итоги в кабинете.",
        ]
    if kind == "budget_stop_pending":
        guard = (
            f" Лимиты в Директе всё равно не дают потратить больше {rubles(money.spendable)}."
            if money
            else ""
        )
        return [
            "Бюджет почти исчерпан, но Яндекс не принял остановку показов. "
            "Мы повторяем попытку каждые несколько минут." + guard,
            "Если хотите остановить сразу, выключите кампании в Директе.",
        ]
    if kind == "budget_near_limit":
        projected = details.get("projected_micros")
        if isinstance(projected, int) and money is not None:
            lines = [
                f"При нынешнем темпе расход дойдёт до {rubles(projected)} "
                f"из {rubles(money.hard_limit)} бюджета месяца."
            ]
        else:
            lines = [NO_NUMBERS]
        return [
            *lines,
            "Мы проверяем расход каждые 5 минут и остановим показы раньше, чем бюджет "
            "будет превышен. Делать ничего не нужно.",
        ]
    if kind == "platform_unavailable":
        guard = (
            f" Бюджет защищён лимитами внутри Директа: больше {rubles(money.spendable)} "
            "реклама не потратит, даже пока мы не на связи."
            if money
            else ""
        )
        return [
            "Яндекс Директ не отвечает на наши запросы." + guard,
            "Мы повторяем проверку сами и напишем, если понадобится ваше действие.",
        ]
    if kind == "platform_not_connected":
        return [
            "Доступ к кабинету Директа не подключён или отозван. Пока его нет, мы не видим "
            "расход и не можем остановить показы при перерасходе.",
            "Подключите кабинет заново в Ad Helper.",
        ]
    if kind == "ads_no_profile":
        return [
            "В профиле бизнеса нет ни одной услуги. Объявления мы пишем только по фактам "
            "из профиля, поэтому новые не появятся, а устаревшие уже сняты.",
            "Добавьте услуги и цены на экране «О бизнесе»: дальше мы всё сделаем сами.",
        ]
    if kind == "ads_need_confirmation":
        return [
            "Вы работаете в нише, где реклама требует отдельной проверки, поэтому новые "
            "объявления мы не запускаем без вас.",
            "Откройте «Объявления» и для каждого нажмите «Запустить» или «Не запускать».",
        ]
    if kind == "ads_launched":
        return _launched(details)
    if kind == "ads_rejected":
        headline = details.get("headline")
        reason = details.get("reason")
        count = details.get("count")
        about = f"«{headline}»" if isinstance(headline, str) and headline else "объявление"
        lines = [
            f"Яндекс отклонил {about}. Мы переписывали его сами, но проверка снова не прошла"
            " или текстом это не исправить.",
        ]
        if isinstance(reason, str) and reason:
            lines.append(f"Причина: {reason}.")
        if isinstance(count, int) and count > 1:
            lines.append(f"Таких объявлений {count}.")
        lines.append(
            "Остальные объявления работают. Откройте отклонённое в разделе «Реклама»:"
            " попросите переделать своими словами или отключите его."
        )
        return lines
    return ["Нужна ваша проверка: подробности в кабинете."]


def compose(
    kind: str,
    business_name: str,
    raised_at: datetime,
    money: MoneyFacts | None,
    details: dict[str, object],
    cabinet_url: str,
) -> Message:
    title = TITLES.get(kind, "Нужна ваша проверка")
    lines = [
        f"{title}.",
        f"Бизнес: {business_name}."
        if kind in NOTICES
        else f"Бизнес: {business_name}. С {_moment(raised_at)}.",
        "",
        *_body(kind, money, details),
        "",
        f"Кабинет: {cabinet_url}",
    ]
    return Message(subject=f"{title} — {business_name}", text="\n".join(lines))
