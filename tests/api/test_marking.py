"""Маркировка через API: ИНН проверяется, в ответе только последние цифры."""

from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import BORIS, Browser, _login
from tests.accounts.test_marking import LEGAL_INN, PERSON_INN
from tests.api.test_site import Anna, anna  # noqa: F401


def _base(business_id: object) -> str:
    return f"/api/businesses/{business_id}/marking"


async def test_owner_saves_marking_and_sees_masked_inn(anna: Anna) -> None:  # noqa: F811
    client, csrf, business_id = anna
    headers = {"X-CSRF-Token": csrf}
    assert (await client.get(_base(business_id))).json() == {"saved": None}
    body = {"kind": "person", "inn": PERSON_INN, "name": "Иванова Анна", "phone": "8 916 123-45-67"}
    saved = await client.put(_base(business_id), json=body, headers=headers)
    assert saved.status_code == 200
    view = (await client.get(_base(business_id))).json()["saved"]
    assert view == {
        "kind": "person",
        "inn": "••••••••2259",
        "name": "Иванова Анна",
        "phone": "+7 ••• •••-45-67",
        "okved": None,
    }
    assert PERSON_INN not in saved.text


async def test_wrong_inn_is_explained_without_echo(anna: Anna) -> None:  # noqa: F811
    client, csrf, business_id = anna
    headers = {"X-CSRF-Token": csrf}
    cases = [
        ({"kind": "legal", "inn": "7707083894", "name": "ООО «Ноготки»"}, "ошибка в цифрах"),
        ({"kind": "ip", "inn": LEGAL_INN, "name": "ИП Иванова"}, "12 цифр"),
        ({"kind": "ip", "inn": PERSON_INN, "name": "ИП Иванова"}, "ОКВЭД"),
        ({"kind": "legal", "inn": "77070", "name": "ООО"}, "10 или 12 цифр"),
        ({"kind": "person", "inn": PERSON_INN, "name": "Иванова Анна"}, "телефон"),
        ({"kind": "legal", "inn": LEGAL_INN, "name": " "}, "наименование"),
    ]
    for body, words in cases:
        response = await client.put(_base(business_id), json=body, headers=headers)
        assert response.status_code == 422, body
        assert words in response.json()["detail"], body
        assert body["inn"] not in response.text
    bad_kind = await client.put(
        _base(business_id), json={"kind": "x", "inn": LEGAL_INN, "name": "ООО"}, headers=headers
    )
    assert bad_kind.status_code == 422
    assert LEGAL_INN not in bad_kind.text
    assert (await client.get(_base(business_id))).json() == {"saved": None}


async def test_marking_needs_csrf_and_owner(
    anna: Anna,  # noqa: F811
    browser: Browser,
    provider: FakeProvider,
) -> None:
    client, _, business_id = anna
    body = {"kind": "legal", "inn": LEGAL_INN, "name": "ООО «Ноготки»", "okved": "96.02"}
    assert (await client.put(_base(business_id), json=body)).status_code == 403
    other = browser()
    csrf = await _login(other, provider, BORIS)
    assert (await other.get(_base(business_id))).status_code == 404
    response = await other.put(_base(business_id), json=body, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 404
