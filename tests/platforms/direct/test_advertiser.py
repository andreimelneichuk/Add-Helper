"""Тесты регистрации рекламодателя и маркировки (AdvertiserRegistry / Clients.update)."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from adhelper.platforms.adapters.direct.platform import DirectPlatform
from adhelper.platforms.adapters.direct.transport import (
    DirectHttpClient,
    DirectRejected,
)
from adhelper.platforms.domain.ports import Advertiser
from tests.platforms.direct.fake_direct import FakeDirect

ACCOUNT = "andreimelneichuk"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


async def _no_sleep(_: float) -> None:
    return None


async def _token(account: str) -> str:
    return f"token-{account}"


def _platform(transport: httpx.AsyncBaseTransport) -> tuple[DirectPlatform, DirectHttpClient]:
    client = DirectHttpClient(transport=transport, sleep=_no_sleep)
    return DirectPlatform(client, _token, lambda: NOW), client


@pytest.fixture
def direct() -> FakeDirect:
    return FakeDirect()


@pytest.fixture
async def platform(direct: FakeDirect) -> AsyncIterator[DirectPlatform]:
    platform, client = _platform(direct.transport())
    yield platform
    await client.aclose()


async def test_set_advertiser_legal_10_digit_inn(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    """10-значный ИНН организации уходит с TinType=LEGAL, именем и ОКВЭД."""
    advertiser = Advertiser(
        kind="legal",
        inn="7704084338",
        name="ООО «Ромашка»",
        phone=None,
        okved="96.02",
    )
    await platform.set_advertiser(ACCOUNT, advertiser)

    assert "clients.update" in direct.calls
    update_req = [r for r in direct.requests if r["name"] == "clients.update"][-1]
    client_payload = update_req["body"]["params"]["Clients"][0]

    assert client_payload["ClientInfo"] == "ООО «Ромашка»"
    assert client_payload["TinInfo"] == {
        "TinType": "LEGAL",
        "Tin": "7704084338",
    }
    assert client_payload["ErirAttributes"]["Organization"]["OkvedCode"] == "96.02"
    assert client_payload["ErirAttributes"]["Organization"]["Name"] == "ООО «Ромашка»"
    assert "Phone" not in client_payload


async def test_set_advertiser_ip_12_digit_inn(platform: DirectPlatform, direct: FakeDirect) -> None:
    """12-значный ИНН ИП уходит с TinType=INDIVIDUAL и ОКВЭД."""
    advertiser = Advertiser(
        kind="ip",
        inn="770708389300",
        name="ИП Иванов Иван Иванович",
        phone=None,
        okved="47.11",
    )
    await platform.set_advertiser(ACCOUNT, advertiser)

    update_req = [r for r in direct.requests if r["name"] == "clients.update"][-1]
    client_payload = update_req["body"]["params"]["Clients"][0]

    assert client_payload["ClientInfo"] == "ИП Иванов Иван Иванович"
    assert client_payload["TinInfo"] == {
        "TinType": "INDIVIDUAL",
        "Tin": "770708389300",
    }
    assert client_payload["ErirAttributes"]["Organization"]["OkvedCode"] == "47.11"


async def test_set_advertiser_person_12_digit_inn_with_phone(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    """Физлицо: 12-значный ИНН с TinType=PHYSICAL, телефоном и без ErirAttributes."""
    advertiser = Advertiser(
        kind="person",
        inn="770708389300",
        name="Иванов Иван",
        phone="+79991234567",
        okved=None,
    )
    await platform.set_advertiser(ACCOUNT, advertiser)

    update_req = [r for r in direct.requests if r["name"] == "clients.update"][-1]
    client_payload = update_req["body"]["params"]["Clients"][0]

    assert client_payload["ClientInfo"] == "Иванов Иван"
    assert client_payload["TinInfo"] == {
        "TinType": "PHYSICAL",
        "Tin": "770708389300",
    }
    assert client_payload["Phone"] == "+79991234567"
    assert "ErirAttributes" not in client_payload


async def test_read_advertiser_parses_clients_get(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    """read_advertiser парсит поля из Clients.get в объект Advertiser."""
    direct.clients[ACCOUNT] = {
        "ClientId": 12345,
        "Login": ACCOUNT,
        "ClientInfo": "ООО «Салон»",
        "Phone": "+74950000000",
        "TinInfo": {
            "TinType": "LEGAL",
            "Tin": "7704084338",
        },
        "ErirAttributes": {
            "Organization": {
                "Name": "ООО «Салон»",
                "OkvedCode": "96.02",
            }
        },
    }

    advertiser = await platform.read_advertiser(ACCOUNT)
    assert advertiser is not None
    assert advertiser.kind == "legal"
    assert advertiser.inn == "7704084338"
    assert advertiser.name == "ООО «Салон»"
    assert advertiser.phone == "+74950000000"
    assert advertiser.okved == "96.02"
    assert advertiser.card() == {
        "kind": "legal",
        "inn_tail": "4338",
        "okved": "96.02",
    }
    get_req = [r for r in direct.requests if r["name"] == "clients.get"][-1]
    assert get_req["body"]["params"]["OrganizationFieldNames"] == ["Name", "OkvedCode"]


async def test_read_advertiser_returns_none_when_empty_or_no_tin(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    """Если реквизиты не заполнены, read_advertiser возвращает None."""
    assert await platform.read_advertiser(ACCOUNT) is None

    direct.clients[ACCOUNT] = {
        "ClientId": 12345,
        "Login": ACCOUNT,
        "ClientInfo": "Без ИНН",
    }
    assert await platform.read_advertiser(ACCOUNT) is None


async def test_set_advertiser_handles_api_error_on_invalid_inn(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    """Отказ API Директа при неверном ИНН вызывает DirectRejected."""
    advertiser = Advertiser(
        kind="legal",
        inn="invalid_inn",
        name="ООО «Ошибка»",
        phone=None,
        okved="96.02",
    )
    with pytest.raises(DirectRejected) as exc_info:
        await platform.set_advertiser(ACCOUNT, advertiser)

    assert exc_info.value.code == 5005
    assert "Неверно указан ИНН" in str(exc_info.value)


async def test_inn_never_leaks_into_logs(
    platform: DirectPlatform, caplog: pytest.LogCaptureFixture
) -> None:
    """Инвариант: полный ИНН не попадает в логи приложения."""
    secret_inn = "7704084338"
    advertiser = Advertiser(
        kind="legal",
        inn=secret_inn,
        name="ООО «Секрет»",
        phone=None,
        okved="96.02",
    )
    with caplog.at_level(logging.DEBUG):
        await platform.set_advertiser(ACCOUNT, advertiser)

    all_logs = " ".join(record.message for record in caplog.records)
    assert secret_inn not in all_logs
