import pytest

from adhelper.commands.adapters.rules_file import load_rules
from adhelper.commands.domain.model import Kind, Known, KnownAd, Source
from adhelper.commands.domain.parse import amounts, by_rules, days_in, from_model

ADS = (
    KnownAd("acc/1", "Маникюр у метро Профсоюзная"),
    KnownAd("acc/2", "Педикюр у метро Профсоюзная"),
    KnownAd("acc/3", "Наращивание ресниц за 2800 ₽"),
)
KNOWN = Known(ADS, ("Маникюр", "Педикюр", "Наращивание ресниц"))
RULES = load_rules("commands-1")


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
