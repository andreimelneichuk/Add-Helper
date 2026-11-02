"""Данные для маркировки: ИНН проверяется контрольной суммой, хранится зашифрованным."""

import uuid

from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import text

from adhelper.accounts.adapters.crypto import AesGcmCipher
from adhelper.accounts.adapters.sql import SqlMarking
from adhelper.accounts.domain.marking import (
    AdvertiserKind,
    Marking,
    MarkingProblem,
    check_marking,
    inn_is_valid,
    mask_inn,
)
from tests.business.test_site_reads_sql import Sessions, anna, boris  # noqa: F401

LEGAL_INN = "7707083893"
PERSON_INN = "500100732259"
W10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)


def _digit(digits: str, weights: tuple[int, ...]) -> str:
    return str(sum(int(d) * w for d, w in zip(digits, weights, strict=False)) % 11 % 10)


def test_known_inns() -> None:
    assert inn_is_valid(LEGAL_INN)
    assert inn_is_valid(PERSON_INN)
    assert not inn_is_valid("7707083894")
    assert not inn_is_valid("500100732258")
    for bad in ("", "123", "77070838931", "770708389x", " 7707083893"):
        assert not inn_is_valid(bad), bad


@given(st.text(alphabet="0123456789", min_size=9, max_size=9))
def test_any_ten_digit_inn_with_right_check_digit_is_valid(head: str) -> None:
    good = head + _digit(head, W10)
    assert inn_is_valid(good)
    wrong = head + str((int(good[-1]) + 1) % 10)
    assert not inn_is_valid(wrong)


def test_kind_decides_inn_length() -> None:
    legal = Marking(AdvertiserKind.LEGAL, PERSON_INN, "ООО «Ноготки»", None, "96.02")
    assert check_marking(legal) is MarkingProblem.INN_LENGTH_FOR_KIND
    ip = Marking(AdvertiserKind.IP, LEGAL_INN, "ИП Иванова А. А.", None, "96.02")
    assert check_marking(ip) is MarkingProblem.INN_LENGTH_FOR_KIND
    assert (
        check_marking(Marking(AdvertiserKind.LEGAL, LEGAL_INN, "ООО «Ноготки»", None, "96.02"))
        is None
    )


def test_person_needs_phone_and_name() -> None:
    no_phone = Marking(AdvertiserKind.PERSON, PERSON_INN, "Иванова Анна", None)
    assert check_marking(no_phone) is MarkingProblem.PHONE
    bad_phone = Marking(AdvertiserKind.PERSON, PERSON_INN, "Иванова Анна", "12345")
    assert check_marking(bad_phone) is MarkingProblem.PHONE
    ok = Marking(AdvertiserKind.PERSON, PERSON_INN, "Иванова Анна", "+7 916 123-45-67")
    assert check_marking(ok) is None
    blank = Marking(AdvertiserKind.IP, PERSON_INN, "  ", None)
    assert check_marking(blank) is MarkingProblem.NAME
    assert check_marking(Marking(AdvertiserKind.IP, "500100732258", "ИП", None)) is (
        MarkingProblem.INN_CHECKSUM
    )


def test_organisation_and_ip_need_okved() -> None:
    for okved in (None, "", "96", "9602", "96.02.1.1", "аб.вг"):
        no_code = Marking(AdvertiserKind.LEGAL, LEGAL_INN, "ООО «Ноготки»", None, okved)
        assert check_marking(no_code) is MarkingProblem.OKVED, okved
    for okved in ("96.02", "47.11.1", "96.0"):
        assert check_marking(Marking(AdvertiserKind.IP, PERSON_INN, "ИП", None, okved)) is None
    # Физическому лицу код не нужен.
    person = Marking(AdvertiserKind.PERSON, PERSON_INN, "Иванова Анна", "+79161234567")
    assert check_marking(person) is None


def test_mask_shows_only_last_digits() -> None:
    assert mask_inn(LEGAL_INN) == "••••••3893"
    assert mask_inn(PERSON_INN) == "••••••••2259"


def _cipher() -> AesGcmCipher:
    return AesGcmCipher([("k1", b"k" * 32)])


async def test_marking_is_stored_encrypted_and_isolated(
    sessions: Sessions,
    anna: uuid.UUID,  # noqa: F811
    boris: uuid.UUID,  # noqa: F811
) -> None:
    business_id, other_id = anna, boris
    store = SqlMarking(sessions, business_id, _cipher())
    assert await store.get() is None
    marking = Marking(AdvertiserKind.PERSON, PERSON_INN, "Иванова Анна", "+79161234567")
    await store.save(marking)
    assert await store.get() == marking
    async with sessions() as session:
        raw = (await session.execute(text("SELECT * FROM advertiser_marking"))).mappings().one()
    # Ни ИНН, ни имя, ни телефон не лежат открытым текстом.
    dump = " ".join(str(v) for v in raw.values())
    for secret in (PERSON_INN, "Иванова", "9161234567"):
        assert secret not in dump
    assert raw["kind"] == "person"
    assert await SqlMarking(sessions, other_id, _cipher()).get() is None
    # Шифртекст другого бизнеса не расшифровать: контекст связан с бизнесом.
    async with sessions() as session:
        await session.execute(
            text(
                "INSERT INTO advertiser_marking (business_id, kind, encrypted_inn, encrypted_name,"
                " encrypted_phone, updated_at) SELECT :other, kind, encrypted_inn, encrypted_name,"
                " encrypted_phone, updated_at FROM advertiser_marking WHERE business_id = :mine"
            ),
            {"other": other_id, "mine": business_id},
        )
        await session.commit()
    assert await SqlMarking(sessions, other_id, _cipher()).get() is None
