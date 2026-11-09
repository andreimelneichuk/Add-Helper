"""Разбор сообщения владельца правилами и проверка ответа модели.

Правила идут первыми: частые просьбы понятны без модели, и ошибка здесь видна в тесте.
Модель зовётся, только когда правила не узнали просьбу, и её ответ проходит те же проверки:
вид из закрытого набора, сумма есть в сообщении, объявление и услуга из списка.
"""

import re
from collections.abc import Mapping, Sequence

from adhelper.commands.domain.model import (
    Command,
    CommandRules,
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
QUESTION_ONE_EACH = (
    "Здесь несколько похожих просьб. Напишите их по одной, например «бюджет 20 000», "
    "а следующим сообщением «отключи объявление про педикюр»."
)
QUESTION_NEGATIVE = (
    "Бюджет не может быть отрицательным. Напишите сумму в месяц, например «бюджет 20 000»."
)
QUESTION_CEILING = (
    "Бюджет больше {limit} ₽ из чата не ставлю: проверьте, нет ли лишнего нуля. "
    "Если сумма верна, поменяйте её в настройках."
)
QUESTION_ZERO = "Бюджет должен быть больше нуля. Чтобы остановить показы, напишите «пауза»."
QUESTION_DELETE = (
    "Объявления не удаляю: они отключаются или архивируются, чтобы всё можно было вернуть. "
    "Напишите «пауза» для всей рекламы или «отключи объявление про …» для одного."
)
NEW_AD_WHERE = (
    "Новые объявления создаются в разделе «Реклама», и тексты берутся только из профиля. "
    "Сам ничего не изменил."
)
QUESTION_ALL_DAYS = (
    "Без показов во все дни — это пауза. Напишите «пауза», если нужно остановить всё."
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


_WORD_VALUES = {
    **{
        w: v
        for v, w in enumerate(
            ("один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"), 1
        )
    },
    "одна": 1,
    "две": 2,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
}
_WORDS = "|".join(sorted(_WORD_VALUES, key=len, reverse=True))
_WORD_THOUSANDS = re.compile(rf"\b((?:(?:{_WORDS})\s+)+)(?:тысяч\S*|тыс\b)")
_DIGITS = re.compile(
    r"(\d{1,3}(?:[  ]\d{3})+|\d+(?:[.,]\d+)?)\s*(тыс\S*|млн\S*|к\b|k\b)?"
    r"(?:\s+(\d{1,3})(?=\s*(?:руб|₽|р\b|$|[,.;?!])))?"
)


def amounts(text: str) -> tuple[int, ...]:
    """Суммы в рублях, как их пишут люди: «15 000», «15 тыс», «15к», «1,5 млн», «сто тысяч»,
    «15 тысяч 500 рублей». Дробь без «тыс» и «млн» — не сумма."""
    found: list[tuple[int, int]] = []
    for match in _WORD_THOUSANDS.finditer(text):
        words = match.group(1).split()
        found.append((match.start(), sum(_WORD_VALUES[w] for w in words) * 1000))
    for match in _DIGITS.finditer(text):
        digits, unit, tail = match.group(1), match.group(2), match.group(3)
        number = digits.replace(" ", "").replace(" ", "").replace(",", ".")
        if unit and unit.startswith("млн"):
            value = round(float(number) * 1_000_000)
        elif unit:
            value = round(float(number) * 1000)
            if unit.startswith("тыс") and tail:
                value += int(tail)
        elif "," in digits or "." in digits:
            continue
        else:
            value = int(number)
        found.append((match.start(), value))
    return tuple(value for _, value in sorted(found))


def scaled_budget(text: str, current: int | None) -> tuple[bool, int | None]:
    """«Увеличь бюджет в два раза»: (просьба есть, новая сумма). Сумму считает код.

    Без известного бюджета суммы нет: спрашиваем её у владельца, а не гадаем.
    """
    if not re.search(r"бюджет", text):
        return False, None
    match = re.search(r"в\s+(\d+|два|три|четыре|полтора)\s+раз|\b(вдвое|втрое)\b", text)
    if match is None:
        return False, None
    word = match.group(1) or match.group(2)
    times = {"два": 2, "вдвое": 2, "три": 3, "втрое": 3, "четыре": 4}.get(word)
    if word == "полтора":
        factor = (3, 2)
    elif times:
        factor = (times, 1)
    elif word.isdigit() and int(word) > 0:
        factor = (int(word), 1)
    else:
        return True, None
    if current is None:
        return True, None
    if re.search(r"уменьш|убав|сниз|сократ|урез|меньш", text):
        return True, current * factor[1] // factor[0]
    if re.search(r"увелич|подним|повыс|больш|добав", text):
        return True, current * factor[0] // factor[1]
    return True, None


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
        key: (len(_stems(name) & words), -len(_stems(name) - words)) for key, name in names.items()
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


def by_rules(message: str, known: Known, max_chars: int, max_budget: int = 500_000) -> Parsed:
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

    if re.search(r"удал\S*", text) and re.search(r"объявлен|реклам", text):
        return _unknown(QUESTION_DELETE)

    if re.search(r"передел|перепиш|короче|другой текст|измени текст", text):
        ad = find_ad(text, known.ads)
        if ad is None:
            return _unknown(QUESTION_AD)
        return Parsed(Command(Kind.REWORK_AD, ad=ad, wish=message.strip()), Source.RULES)

    asks_budget = re.search(r"какой|каков\S*|сколько|остал\S*|текущ\S*|покажи", text)
    sets_budget = re.search(
        r"постав|установ|смени|измени|сделай|увелич|уменьш|убав|подним|снизь", text
    )
    if re.search(r"бюджет", text) and asks_budget and not sets_budget and not numbers:
        return Parsed(Command(Kind.BUDGET), Source.RULES)
    if re.search(r"бюджет|тратить|трать", text):
        if scaled_budget(text, None)[0]:
            return _unknown(QUESTION_BUDGET)
        if re.search(r"(?<![\w])-\s?\d|минус", text):
            return _unknown(QUESTION_NEGATIVE)
        if not numbers:
            return _unknown(QUESTION_BUDGET)
        if numbers[0] <= 0:
            return _unknown(QUESTION_ZERO)
        if numbers[0] > max_budget:
            return _unknown(QUESTION_CEILING.format(limit=f"{max_budget:,}".replace(",", " ")))
        return Parsed(Command(Kind.SET_BUDGET, rubles=numbers[0]), Source.RULES)

    service = find_service(text, known.services)
    priced = re.search(r"цен|стоит|стоимост|подорож|подешев|₽|руб|теперь", text)
    if priced and service is not None:
        if not numbers:
            return _unknown(QUESTION_PRICE.format(service=service.lower()))
        return Parsed(Command(Kind.SET_PRICE, service=service, rubles=numbers[0]), Source.RULES)

    new_ad = re.search(
        r"(запусти|создай|сделай|добавь|напиши)\S*\s+(?:нов\S+\s+)?(?:реклам|объявлен)\S*"
        r"(?:\s+(?:про|на|для|по|о)\s+(.+))?",
        text,
    )
    if new_ad and ("нов" in new_ad.group(0) or new_ad.group(2)):
        tail = new_ad.group(2)
        if tail and find_service(tail, known.services) is None:
            return _unknown(QUESTION_SERVICE)
        return _unknown(NEW_AD_WHERE)

    # «Отключи рекламу с педикюром» — про одно объявление, а не про всю рекламу.
    qualified = _qualified_ad(text, known)
    if qualified is not None:
        return qualified
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


AD_OFF_WORDS = r"пауз|останов|выключ|отключ|убери|убрать|сними"
AD_ON_WORDS = r"включ|возобнов|запусти|верни"
QUALIFIED = re.compile(r"(?:реклам|показ)\S*\s+(?:(?:с|со|про|на|для|по|о|об|у|в)\s+)?(.+)")


def _qualified_ad(text: str, known: Known) -> Parsed | None:
    """«Отключи рекламу с педикюром»: уточнение после «рекламу» указывает на объявление.

    Уточнение, похожее на слова объявлений, но не на одно из них, — вопрос: остановить
    всю рекламу вместо одного объявления дороже, чем переспросить. «На неделю» и другие
    слова не из объявлений остаются общей командой.
    """
    match = QUALIFIED.search(text)
    off = re.search(AD_OFF_WORDS, text)
    on = re.search(AD_ON_WORDS, text) and not off
    if match is None or not (off or on):
        return None
    tail = match.group(1)
    ad = find_ad(tail, known.ads)
    if ad is not None:
        return Parsed(Command(Kind.AD_OFF if off else Kind.AD_ON, ad=ad), Source.RULES)
    words = _stems(tail)
    if any(_stems(ad.name) & words for ad in known.ads):
        return _unknown(QUESTION_AD)
    return None


# Границы просьб: знак препинания с пробелом после (не «1,5 тыс»), «и», «потом», «затем», «ещё».
SPLIT = re.compile(
    r"\s*[;,.](?=\s|$)\s*(?:а\s+)?(?:потом\s+|затем\s+|еще\s+|и\s+)?"
    r"|\s+(?:а\s+)?(?:потом|затем|и|а также|еще)\s+"
)


def parse_all(message: str, known: Known, rules: CommandRules) -> tuple[Parsed, ...]:
    """Несколько просьб в одном сообщении, каждая своей командой.

    Сообщение делим, только если правила узнали каждую часть: «в субботу и воскресенье» —
    одна просьба. Часть с уточнением останавливает всё сообщение: половину не выполняем.
    Две команды одного вида или больше max_requests — просим писать по одной.
    """
    whole = by_rules(message, known, rules.max_chars, rules.max_budget)
    if rules.max_requests <= 1 or len(message) > rules.max_chars:
        return (whole,)
    parts = [part for part in SPLIT.split(normalize(message)) if part]
    parsed: list[Parsed] = []
    for i, part in enumerate(parts):
        one = by_rules(part, known, rules.max_chars, rules.max_budget)
        if one.command.kind is Kind.REWORK_AD:
            # Переделка забирает остаток как пожелание: «… и сделай короче» — не вторая просьба.
            if i == 0:
                return (whole,)
            # Части взяты из нормализованного текста, хвост — оттуда же, с запятыми.
            text = normalize(message)
            tail = text[text.rfind(part) :]
            parsed.append(by_rules(tail, known, rules.max_chars, rules.max_budget))
            break
        parsed.append(one)
    if len(parsed) < 2:
        return (whole,)
    if any(p.command.kind is Kind.UNKNOWN and not p.question for p in parsed):
        return (whole,)
    question = next((p for p in parsed if p.question), None)
    if question is not None:
        return (question,)
    kinds = [p.command.kind for p in parsed]
    if len(parsed) > rules.max_requests or len(set(kinds)) < len(kinds):
        if kinds.count(Kind.SET_PRICE) == len(kinds) and len(parsed) <= rules.max_requests:
            services = [p.command.service for p in parsed]
            if len(set(services)) == len(services):
                return tuple(parsed)
        return (_unknown(QUESTION_ONE_EACH),)
    return tuple(parsed)


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


def from_model(
    answer: Mapping[str, object], message: str, known: Known, max_budget: int = 500_000
) -> Parsed:
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

    if kind is Kind.BUDGET:
        return unknown()
    if kind in (Kind.SET_BUDGET, Kind.SET_PRICE):
        # Суммы не додумываем: число должно стоять в сообщении.
        if rubles is None or rubles not in amounts(text):
            return unknown(QUESTION_BUDGET if kind is Kind.SET_BUDGET else "")
        if kind is Kind.SET_BUDGET:
            if rubles > max_budget:
                limit = f"{max_budget:,}".replace(",", " ")
                return unknown(QUESTION_CEILING.format(limit=limit))
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
