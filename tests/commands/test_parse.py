import pytest

from adhelper.commands.adapters.rules_file import load_rules
from adhelper.commands.domain.model import Kind, Known, KnownAd, Source
from adhelper.commands.domain.parse import (
    QUESTION_AD,
    QUESTION_ONE_EACH,
    amounts,
    by_rules,
    days_in,
    from_model,
    parse_all,
)

ADS = (
    KnownAd("acc/1", "Маникюр у метро Профсоюзная"),
    KnownAd("acc/2", "Педикюр у метро Профсоюзная"),
    KnownAd("acc/3", "Наращивание ресниц за 2800 ₽"),
)
KNOWN = Known(ADS, ("Маникюр", "Педикюр", "Наращивание ресниц"))
RULES = load_rules("commands-1")
RULES2 = load_rules("commands-2")


def parse(text: str):  # type: ignore[no-untyped-def]
    return by_rules(text, KNOWN, RULES.max_chars)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("бюджет 15 000", (15000,)),
        ("15000 ₽", (15000,)),
        ("15 тыс", (15000,)),
        ("1,5 тыс", (1500,)),
        ("20к", (20000,)),
        ("без суммы", ()),
    ],
)
def test_amounts(text: str, expected: tuple[int, ...]) -> None:
    assert amounts(text) == expected


def test_days() -> None:
    assert days_in("не показывай по воскресеньям") == (7,)
    assert days_in("по выходным") == (6, 7)
    assert days_in("в среду и пятницу") == (3, 5)
    assert days_in("средний чек") == ()


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Пауза", Kind.PAUSE),
        ("останови рекламу", Kind.PAUSE),
        ("включи снова", Kind.RESUME),
        ("отмени последнее", Kind.UNDO_LAST),
        ("что ты сделал за неделю", Kind.HISTORY),
        ("как дела", Kind.STATS),
    ],
)
def test_simple_commands(text: str, kind: Kind) -> None:
    parsed = parse(text)
    assert parsed.command.kind is kind
    assert parsed.source is Source.RULES


def test_skip_days() -> None:
    assert parse("не показывай рекламу по воскресеньям").command.days == (7,)
    assert parse("без выходных").command.days == (6, 7)
    # «Только по будням» — значит без субботы и воскресенья.
    assert parse("показывай только по будням").command.days == (6, 7)
    assert parse("в понедельник реклама нормально").command.kind is Kind.UNKNOWN


def test_all_days_is_not_a_schedule() -> None:
    parsed = parse("не показывай по будням и выходным")
    assert parsed.command.kind is Kind.UNKNOWN
    assert "пауза" in parsed.question


def test_budget_needs_the_amount() -> None:
    assert parse("бюджет 20 000 в месяц").command.rubles == 20000
    parsed = parse("увеличь бюджет")
    assert parsed.command.kind is Kind.UNKNOWN
    assert parsed.question


def test_price_matches_profile_service() -> None:
    parsed = parse("педикюр теперь 1600")
    assert parsed.command.kind is Kind.SET_PRICE
    assert parsed.command.service == "Педикюр"
    assert parsed.command.rubles == 1600
    assert parse("цена на маникюр выросла").question


def test_service_named_fully_wins_over_longer_name() -> None:
    # Сайт добавил «… гель-лаком»: «маникюр с покрытием» — это короткая услуга, а не ничья.
    services = ("Маникюр с покрытием", "Маникюр с покрытием гель-лаком")
    known = Known((), services)
    short = by_rules("маникюр с покрытием теперь 1600", known, RULES.max_chars)
    assert short.command.service == "Маникюр с покрытием"
    long = by_rules("маникюр с покрытием гель-лаком теперь 1900", known, RULES.max_chars)
    assert long.command.service == "Маникюр с покрытием гель-лаком"


def test_ad_by_title_words() -> None:
    parsed = parse("отключи объявление про ресницы")
    assert parsed.command.kind is Kind.AD_OFF
    assert parsed.command.ad == ADS[2]
    assert parse("включи объявление про педикюр").command.ad == ADS[1]
    # Слово есть у двух объявлений поровну: не угадываем.
    assert parse("отключи объявление у метро").question


def test_rework_keeps_the_wish() -> None:
    parsed = parse("Переделай объявление про педикюр короче")
    assert parsed.command.kind is Kind.REWORK_AD
    assert parsed.command.ad == ADS[1]
    assert parsed.command.wish == "Переделай объявление про педикюр короче"


def test_long_message_asks_to_shorten() -> None:
    parsed = parse("пауза " * 100)
    assert parsed.command.kind is Kind.UNKNOWN
    assert parsed.question


def test_unknown_leaves_the_question_empty_for_the_model() -> None:
    parsed = parse("сделай как у конкурентов")
    assert parsed.command.kind is Kind.UNKNOWN
    assert parsed.question == ""


def test_model_amount_must_be_in_the_message() -> None:
    message = "хочу тратить поменьше, тысяч десять"
    assert from_model({"command": "set_budget", "rubles": 10000}, message, KNOWN).command.kind is (
        Kind.UNKNOWN
    )
    ok = from_model({"command": "set_budget", "rubles": 10000}, "давай 10 000", KNOWN)
    assert ok.command.rubles == 10000
    assert ok.source is Source.MODEL


def test_model_cannot_name_unknown_things() -> None:
    assert from_model({"command": "drop_table"}, "x", KNOWN).command.kind is Kind.UNKNOWN
    assert from_model({"command": "ad_off", "ad": 7}, "x", KNOWN).command.kind is Kind.UNKNOWN
    assert from_model({"command": "ad_off", "ad": True}, "x", KNOWN).command.kind is Kind.UNKNOWN
    price = {"command": "set_price", "rubles": 900, "service": "Стрижка"}
    assert from_model(price, "стрижка 900", KNOWN).command.kind is Kind.UNKNOWN
    days = {"command": "skip_days", "days": [7]}
    assert from_model(days, "не работаем в праздники", KNOWN).command.kind is Kind.UNKNOWN
    assert from_model(days, "в воскресенье выходной", KNOWN).command.days == (7,)


def test_model_ad_by_number() -> None:
    parsed = from_model({"command": "ad_off", "ad": 1}, "убери ту, что про ноги", KNOWN)
    assert parsed.command.ad == ADS[1]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("сними паузу", Kind.RESUME),
        ("включи рекламу обратно", Kind.RESUME),
        ("выключи всю рекламу", Kind.PAUSE),
        ("стоп", Kind.PAUSE),
        ("убери ту, что про руки", Kind.UNKNOWN),
        ("включи", Kind.RESUME),
    ],
)
def test_pause_needs_plain_words(text: str, kind: Kind) -> None:
    assert parse(text).command.kind is kind


def test_ad_named_after_advertising_word_is_not_a_full_pause() -> None:
    # Ошибка: «отключи рекламу с педикюром» правила понимали как паузу всей рекламы.
    off = parse("отключи рекламу с педикюром")
    assert off.command.kind is Kind.AD_OFF
    assert off.command.ad == ADS[1]
    assert parse("выключи рекламу про маникюр").command.ad == ADS[0]
    assert parse("включи рекламу на ресницы").command.kind is Kind.AD_ON
    # Слово похоже на объявления, но не одно из них: спрашиваем, а не останавливаем всё.
    assert parse("отключи рекламу у метро").question == QUESTION_AD
    assert parse("отключи рекламу").command.kind is Kind.PAUSE
    assert parse("включи обратно рекламу ресниц").command.ad == ADS[2]
    assert parse("включи рекламу обратно").command.kind is Kind.RESUME
    assert parse("останови рекламу на неделю").command.kind is Kind.PAUSE


def kinds(text: str) -> list[Kind]:
    return [p.command.kind for p in parse_all(text, KNOWN, RULES2)]


def test_two_requests_in_one_message() -> None:
    parsed = parse_all("убавь бюджет до 10к и отключи рекламу с педикюром", KNOWN, RULES2)
    assert [p.command.kind for p in parsed] == [Kind.SET_BUDGET, Kind.AD_OFF]
    assert parsed[0].command.rubles == 10000
    assert parsed[1].command.ad == ADS[1]
    assert kinds("поставь бюджет 20 000, потом включи рекламу") == [Kind.SET_BUDGET, Kind.RESUME]
    assert kinds("маникюр теперь 1600; педикюр теперь 2100") == [Kind.SET_PRICE, Kind.SET_PRICE]
    # Переделка забирает всё, что после неё, как пожелание; просьба до неё не теряется.
    both = parse_all(
        "подними бюджет до 30000, а еще переделай объявление про ресницы", KNOWN, RULES2
    )
    assert [p.command.kind for p in both] == [Kind.SET_BUDGET, Kind.REWORK_AD]
    assert both[1].command.ad == ADS[2]


def test_one_request_with_and_stays_whole() -> None:
    assert parse_all("не показывай в субботу и воскресенье", KNOWN, RULES2)[0].command.days == (
        6,
        7,
    )
    assert kinds("не показывай в субботу и воскресенье") == [Kind.SKIP_DAYS]
    rework = parse_all("переделай объявление про педикюр и сделай короче", KNOWN, RULES2)
    assert [p.command.kind for p in rework] == [Kind.REWORK_AD]
    assert rework[0].command.wish == "переделай объявление про педикюр и сделай короче"
    assert kinds("сделай как у конкурентов и побыстрее") == [Kind.UNKNOWN]
    assert parse_all("бюджет 1,5 тыс", KNOWN, RULES2)[0].command.rubles == 1500


def test_unclear_part_stops_the_whole_message() -> None:
    # Половину просьбы не выполняем: сначала уточнение.
    parsed = parse_all("бюджет 10к и отключи рекламу у метро", KNOWN, RULES2)
    assert len(parsed) == 1 and parsed[0].question == QUESTION_AD
    same = parse_all("бюджет 10к и бюджет 20к", KNOWN, RULES2)
    assert len(same) == 1 and same[0].question == QUESTION_ONE_EACH
    many = parse_all("пауза, включи, бюджет 10к, как дела", KNOWN, RULES2)
    assert len(many) == 1 and many[0].question == QUESTION_ONE_EACH
