"""Разбор сайта до входа на настоящем PostgreSQL: доступ по ключу, лимит по адресу и общий."""

import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from adhelper.business.adapters.site_reads import (
    SqlGuestSiteReads,
    SqlSiteReads,
    TooManyGuestReads,
    TooManyReads,
)
from adhelper.business.application.site import ReadStatus
from tests.business.test_read_site import GOOD, Model
from tests.business.test_site_reads_sql import NOW, RULES, Sessions, _run, anna  # noqa: F401

TOKEN = hashlib.sha256(b"token-1").hexdigest()


def _token(n: int) -> str:
    return hashlib.sha256(f"token-{n}".encode()).hexdigest()


async def test_guest_read_is_queued_and_seen_only_with_its_token(sessions: Sessions) -> None:
    guests = SqlGuestSiteReads(sessions)
    read_id = await guests.request("https://nogti.ru/", "addr-1", TOKEN, RULES, NOW)
    waiting = await guests.get(read_id, TOKEN)
    assert waiting is not None and waiting.status is ReadStatus.QUEUED

    assert await _run(sessions, Model(GOOD)) == 1
    done = await guests.get(read_id, TOKEN)
    assert done is not None and done.status is ReadStatus.READY
    assert [f.value for f in done.facts] == ["маникюр", "Маникюр с покрытием"]
    # Чужой ключ и чужой номер ничего не показывают.
    assert await guests.get(read_id, _token(2)) is None
    assert await guests.get(read_id + 1, TOKEN) is None


async def test_guest_read_is_not_visible_to_a_business(
    sessions: Sessions,
    anna: uuid.UUID,  # noqa: F811
) -> None:
    await SqlGuestSiteReads(sessions).request("https://nogti.ru/", "addr-1", TOKEN, RULES, NOW)
    assert await SqlSiteReads(sessions, anna).latest() is None


async def test_address_is_stored_only_as_key(sessions: Sessions) -> None:
    await SqlGuestSiteReads(sessions).request("https://nogti.ru/", "addr-1", TOKEN, RULES, NOW)
    async with sessions() as session:
        row = (
            await session.execute(text("SELECT business_id, guest_key, token_hash FROM site_reads"))
        ).one()
    assert tuple(row) == (None, "addr-1", TOKEN)


async def test_limit_per_address_in_sliding_day(sessions: Sessions) -> None:
    guests = SqlGuestSiteReads(sessions)
    for n in range(RULES.max_guest_reads_per_address):
        at = NOW + timedelta(hours=n)
        await guests.request(f"https://nogti.ru/{n}", "addr-1", _token(n), RULES, at)
        await _run(sessions, None, at)
    late = NOW + timedelta(hours=10)
    with pytest.raises(TooManyReads):
        await guests.request("https://nogti.ru/x", "addr-1", _token(99), RULES, late)
    # Другой адрес лимит не делит.
    await guests.request("https://nogti.ru/x", "addr-2", _token(98), RULES, late)
    # Через сутки после первого разбора этот адрес снова может.
    await guests.request(
        "https://nogti.ru/y", "addr-1", _token(97), RULES, NOW + timedelta(hours=24, minutes=1)
    )


async def test_waiting_read_counts_against_address(sessions: Sessions) -> None:
    guests = SqlGuestSiteReads(sessions)
    for n in range(RULES.max_guest_reads_per_address):
        await guests.request(f"https://nogti.ru/{n}", "addr-1", _token(n), RULES, NOW)
    # У гостя нет постоянного ключа: ждущие разборы тоже расходуют лимит.
    with pytest.raises(TooManyReads):
        await guests.request("https://nogti.ru/x", "addr-1", _token(99), RULES, NOW)


async def test_total_guest_limit_protects_model_spend(sessions: Sessions) -> None:
    rules = RULES.__class__(**{**RULES.__dict__, "max_guest_reads_per_day": 2})
    guests = SqlGuestSiteReads(sessions)
    await guests.request("https://a.ru/", "addr-1", _token(1), rules, NOW)
    await guests.request("https://b.ru/", "addr-2", _token(2), rules, NOW)
    with pytest.raises(TooManyGuestReads):
        await guests.request("https://c.ru/", "addr-3", _token(3), rules, NOW)
