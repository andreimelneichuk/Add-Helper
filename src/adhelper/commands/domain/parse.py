"""Разбор сообщения владельца правилами и проверка ответа модели.

Правила идут первыми: частые просьбы понятны без модели, и ошибка здесь видна в тесте.
Модель зовётся, только когда правила не узнали просьбу, и её ответ проходит те же проверки:
вид из закрытого набора, сумма есть в сообщении, объявление и услуга из списка.
"""

import re
from collections.abc import Mapping, Sequence

from adhelper.commands.domain.model import (
    Command,
    Kind,
    Known,
    KnownAd,
    Parsed,
    Source,
)

WEEK = (1, 2, 3, 4, 5, 6, 7)
DAY_NAMES = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
# Основы дней недели: «по средам», «в субботу», «воскресенье». Среду ищем по словоформам,
# чтобы «средний» и «средства» не стали днём.
DAY_PATTERNS = (
    (1, r"понедельн"),
    (2, r"вторн"),
    (3, r"\bсред(а|у|ы|ам|е)\b"),
    (4, r"четверг"),
    (5, r"пятниц"),
    (6, r"суббот"),
    (7, r"воскресен|\bвс\b"),
)
NEGATIVE = r"\bне\s+(показ|крут|работ|запуск|вклю)|\bбез\b|отключ|выключ|убери|убрать|кроме|\bнет\b"

QUESTION_BUDGET = "Какой бюджет поставить? Напишите сумму в месяц, например «бюджет 20 000»."
QUESTION_PRICE = "Какая теперь цена? Напишите сумму, например «{service} теперь 1600»."
QUESTION_AD = (
    "Какое объявление? Напишите слово из его заголовка или откройте его в разделе «Реклама»."
)
QUESTION_SERVICE = (
    "Такой услуги нет в профиле. Добавьте её в профиль, тогда смогу менять цену из чата."
)
QUESTION_LONG = (
    "Сообщение длинное. Напишите одну просьбу коротко, например «пауза» или «бюджет 20 000»."
)
QUESTION_ALL_DAYS = (
    "Без показов во все дни — это пауза. Напишите «пауза», если нужно остановить всё."
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


def amounts(text: str) -> tuple[int, ...]:
    """Суммы в рублях, как их пишут люди: «15 000», «15000 ₽», «15 тыс», «15к», «1,5 тыс»."""
    found: list[int] = []
    pattern = r"(\d{1,3}(?:[  ]\d{3})+|\d+(?:[.,]\d+)?)\s*(тыс\S*|к\b|k\b)?"
    for match in re.finditer(pattern, text):
        digits, thousands = match.group(1), match.group(2)
        if thousands:
            value = float(digits.replace(" ", "").replace(" ", "").replace(",", "."))
            found.append(round(value * 1000))
        elif "," in digits or "." in digits:
            continue
        else:
            found.append(int(digits.replace(" ", "").replace(" ", "")))
    return tuple(found)


def days_in(text: str) -> tuple[int, ...]:
    days = {day for day, pattern in DAY_PATTERNS if re.search(pattern, text)}
    if "выходн" in text:
        days |= {6, 7}
    if "будн" in text:
        days |= {1, 2, 3, 4, 5}
    return tuple(sorted(days))


def _stems(name: str) -> set[str]:
    # Пять первых букв слова: «маникюр» узнаётся в «маникюра», «педикюр» в «педикюру».
    return {word[:5] for word in re.findall(r"[a-zа-я]{4,}", normalize(name))}


def _best(text: str, names: Mapping[str, str]) -> str | None:
    """Ключ единственного лучшего совпадения по основам слов; ничья — не угадываем.

    При равном числе совпавших слов выигрывает название, названное целиком: «маникюр
    с покрытием» — это услуга с таким именем, а не «… гель-лаком» с лишними словами.
    """
    words = _stems(text)
    scores = {
        key: (len(_stems(name) & words), -len(_stems(name) - words))
        for key, name in names.items()
    }
    top = max(scores.values(), default=(0, 0))
    if top[0] == 0:
        return None
    best = [key for key, score in scores.items() if score == top]
    return best[0] if len(best) == 1 else None


def find_ad(text: str, ads: Sequence[KnownAd]) -> KnownAd | None:
    key = _best(text, {ad.id: ad.name for ad in ads})
    return next((ad for ad in ads if ad.id == key), None)


def find_service(text: str, services: Sequence[str]) -> str | None:
    return _best(text, {name: name for name in services})


def _unknown(question: str = "") -> Parsed:
    return Parsed(Command(Kind.UNKNOWN), Source.RULES, question)


def by_rules(message: str, known: Known, max_chars: int) -> Parsed:
    """Разбор правилами. UNKNOWN без вопроса значит «правила не узнали, можно спросить модель»."""
    if len(message) > max_chars:
        return _unknown(QUESTION_LONG)
    text = normalize(message)
    numbers = amounts(text)

    if re.search(r"истори|что (ты )?(делал|сделал|менял)", text):
        return Parsed(Command(Kind.HISTORY), Source.RULES)
    if re.search(r"статист|как дела|итог|сколько (заяв|клиент|обращ)", text):
        return Parsed(Command(Kind.STATS), Source.RULES)
    if re.search(r"отмени|верни как было", text):
        return Parsed(Command(Kind.UNDO_LAST), Source.RULES)

    days = days_in(text)
    if days:
        skip = tuple(d for d in WEEK if d not in days) if "только" in text else days
        if "только" in text or re.search(NEGATIVE, text):
            if len(skip) == len(WEEK):
                return _unknown(QUESTION_ALL_DAYS)
            if skip:
                return Parsed(Command(Kind.SKIP_DAYS, days=skip), Source.RULES)

    if re.search(r"передел|перепиш|короче|другой текст|измени текст", text):
        ad = find_ad(text, known.ads)
        if ad is None:
            return _unknown(QUESTION_AD)
        return Parsed(Command(Kind.REWORK_AD, ad=ad, wish=message.strip()), Source.RULES)

    if re.search(r"бюджет|тратить|трать", text):
        if not numbers:
            return _unknown(QUESTION_BUDGET)
        return Parsed(Command(Kind.SET_BUDGET, rubles=numbers[0]), Source.RULES)

    service = find_service(text, known.services)
    priced = re.search(r"цен|стоит|стоимост|подорож|подешев|₽|руб|теперь", text)
    if priced and service is not None:
        if not numbers:
            return _unknown(QUESTION_PRICE.format(service=service.lower()))
        return Parsed(Command(Kind.SET_PRICE, service=service, rubles=numbers[0]), Source.RULES)

    # Объявление: глагол и слово «объявление». Вся реклама: только явные слова, чтобы «убери
    # ту, что про руки» не стало паузой всего; такое разберёт модель или уточнит владелец.
    ad_off = re.search(r"пауз|останов|выключ|отключ|убери|убрать|сними", text)
    ad_on = re.search(r"включ|возобнов|запусти|верни", text) and not ad_off
    if (ad_off or ad_on) and "объявлен" in text:
        ad = find_ad(text, known.ads)
        if ad is None:
            return _unknown(QUESTION_AD)
        return Parsed(Command(Kind.AD_OFF if ad_off else Kind.AD_ON, ad=ad), Source.RULES)
    pause = re.search(r"пауз|\bстоп\b|останов|(выключ|отключ)\S*( вс[юеё])? (реклам|показ)", text)
    resume = re.search(
        r"(сними|снять|убери) пауз|возобнов|(включ|запусти)\S*( снова| обратно)?"
        r"( вс[юеё])?( реклам| показ|$)",
        text,
    )
    if resume:
        return Parsed(Command(Kind.RESUME), Source.RULES)
    if pause:
        return Parsed(Command(Kind.PAUSE), Source.RULES)
    return _unknown()


# Схема ответа модели: только поля закрытого набора. Объявление — по номеру из списка,
# чтобы модель не могла вернуть чужой адрес.
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "enum": [kind.value for kind in Kind]},
        "rubles": {"type": ["integer", "null"]},
        "ad": {"type": ["integer", "null"]},
        "service": {"type": ["string", "null"]},
        "days": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 7}},
    },
    "required": ["command"],
    "additionalProperties": False,
}


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def from_model(answer: Mapping[str, object], message: str, known: Known) -> Parsed:
    """Проверить ответ модели. Всё, что не подтверждается сообщением и списками, — UNKNOWN."""
    try:
        kind = Kind(str(answer.get("command")))
    except ValueError:
        return Parsed(Command(Kind.UNKNOWN), Source.MODEL)
    text = normalize(message)
    rubles = _int(answer.get("rubles"))
    ad_index = _int(answer.get("ad"))
    service = answer.get("service")
    raw_days = answer.get("days") or []
    days = (
        tuple(sorted({d for d in raw_days if _int(d) in WEEK}))
        if isinstance(raw_days, list)
        else ()
    )

    def unknown(question: str = "") -> Parsed:
        return Parsed(Command(Kind.UNKNOWN), Source.MODEL, question)

    if kind in (Kind.SET_BUDGET, Kind.SET_PRICE):
        # Суммы не додумываем: число должно стоять в сообщении.
        if rubles is None or rubles not in amounts(text):
            return unknown(QUESTION_BUDGET if kind is Kind.SET_BUDGET else "")
        if kind is Kind.SET_BUDGET:
            return Parsed(Command(kind, rubles=rubles), Source.MODEL)
        if not isinstance(service, str) or service not in known.services:
            return unknown(QUESTION_SERVICE)
        return Parsed(Command(kind, rubles=rubles, service=service), Source.MODEL)
    if kind is Kind.SKIP_DAYS:
        # День должен быть назван в сообщении, иначе модель могла его придумать.
        if not days or not days_in(text):
            return unknown()
        if len(days) == len(WEEK):
            return unknown(QUESTION_ALL_DAYS)
        return Parsed(Command(kind, days=days), Source.MODEL)
    if kind in (Kind.AD_OFF, Kind.AD_ON, Kind.REWORK_AD):
        if ad_index is None or not 0 <= ad_index < len(known.ads):
            return unknown(QUESTION_AD)
        wish = message.strip() if kind is Kind.REWORK_AD else ""
        return Parsed(Command(kind, ad=known.ads[ad_index], wish=wish), Source.MODEL)
    return Parsed(Command(kind), Source.MODEL)
