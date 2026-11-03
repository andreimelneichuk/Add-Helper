"""Тексты ответа на команду. Числа и названия подставляет код, а не модель."""

from adhelper.commands.domain.model import Command, Kind
from adhelper.commands.domain.parse import DAY_NAMES

REFUSAL = (
    "Этого я пока не умею, поэтому ничего не изменил. Могу поставить на паузу и снять её, "
    "сменить бюджет месяца, не показывать рекламу в выбранные дни, отключить или включить "
    "объявление, переделать его, сменить цену услуги в профиле, отменить последнее изменение "
    "и рассказать цифры."
)
# Ближайшая настройка для частых просьб вне набора: честный отказ лучше догадки.
NEAREST = (
    (
        ("скидк", "акци"),
        "Скидки и акции беру только из профиля: добавьте её в профиль, "
        "и я предложу объявление с ней. Сам ничего не изменил.",
    ),
    (
        ("район", "город", "регион", "метро"),
        "Район показов меняется в разделе «Реклама», в настройках показов. Сам ничего не изменил.",
    ),
    (
        ("час", "время", "утр", "вечер"),
        "Часы показов меняются в разделе «Реклама», в настройках показов. Сам ничего не изменил.",
    ),
    (("услуг",), "Услуги и цены меняются в профиле. Сам ничего не изменил."),
)


def _rub(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₽"


def _days(days: tuple[int, ...]) -> str:
    return ", ".join(DAY_NAMES[day - 1] for day in days)


def meaning(command: Command) -> str:
    """Как код понял команду, для вопроса «Применить?»."""
    match command.kind:
        case Kind.PAUSE:
            return "остановить все показы. Настройки сохранятся."
        case Kind.RESUME:
            return "снять паузу. Перед включением проверю, что расход не выйдет за бюджет."
        case Kind.SET_BUDGET:
            return f"бюджет {_rub(command.rubles or 0)} в месяц."
        case Kind.SET_PRICE:
            return (
                f"«{command.service}» теперь {_rub(command.rubles or 0)}. Обновлю профиль, "
                "объявления со старой ценой заменю после вашей проверки."
            )
        case Kind.SKIP_DAYS:
            return f"не показывать рекламу: {_days(command.days)}. Бюджет месяца не изменится."
        case Kind.AD_OFF:
            name = command.ad.name if command.ad else ""
            return f"отключить объявление «{name}». Общий бюджет не изменится."
        case Kind.AD_ON:
            name = command.ad.name if command.ad else ""
            return f"включить объявление «{name}»."
        case Kind.UNDO_LAST:
            return "отменить последнее изменение."
        case _:
            return ""


def refusal(message: str) -> str:
    text = message.lower()
    for words, answer in NEAREST:
        if any(word in text for word in words):
            return answer
    return REFUSAL
