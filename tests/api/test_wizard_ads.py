"""Шаг «Объявления» мастера: писатель до входа, выбор переносится в бизнес после входа."""

import asyncio

import httpx
import pytest

from adhelper.creatives.adapters.rules_file import load_rules, load_wizard_rules
from adhelper.creatives.adapters.sql import SqlCreativeQueue, SqlProfileSource
from adhelper.creatives.application.service import run_queued
from adhelper.creatives.application.wizard import CURRENT_WIZARD_RULES
from adhelper.ledger.adapters.sql import SqlChangeRepository
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus, ProposedChange
from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import ANNA, BORIS, Browser, _connect, _login
from tests.api.test_cabinet import NOW, Sessions
from tests.api.test_guest_site import Guest, guest  # noqa: F401
from tests.api.test_site import Anna, anna  # noqa: F401
from tests.creatives.test_service import GOOD, SECOND, FakeModel, ads

WIZARD = load_wizard_rules(CURRENT_WIZARD_RULES)
DRAFT = {
    "niche": "маникюр",
    "address": "ул. Ленина, 5",
    "services": [{"name": "Маникюр", "price": 1500}],
    "features": ["Стерильные инструменты"],
}


async def write(sessions: Sessions, model: FakeModel | None = None) -> None:
    await run_queued(
        SqlCreativeQueue(sessions),
        SqlProfileSource(sessions),
        model or FakeModel(ads(GOOD, SECOND)),
        None,
        load_rules,
        lambda: NOW,
    )


async def written(client: httpx.AsyncClient, sessions: Sessions) -> tuple[int, str]:
    queued = await client.post("/api/creative-sets", json=DRAFT)
    assert queued.status_code == 202, queued.text
    body = queued.json()
    await write(sessions)
    return body["id"], body["token"]


async def test_guest_gets_ads_written_from_draft(guest: Guest, sessions: Sessions) -> None:  # noqa: F811
    client = guest("203.0.113.5")
    queued = await client.post("/api/creative-sets", json=DRAFT)
    assert queued.status_code == 202
    body = queued.json()
    assert body["status"] == "queued" and len(body["token"]) >= 32
    path = f"/api/creative-sets/{body['id']}"
    headers = {"X-Set-Token": body["token"]}
    assert (await client.get(path, headers=headers)).json()["status"] == "queued"

    await write(sessions)
    view = (await client.get(path, headers=headers)).json()
    assert view["status"] == "ready"
    assert view["source"] == "model"
    assert [a["headline"] for a in view["ads"]] == [GOOD["headline"], SECOND["headline"]]
    # Владелец видит, на какие свои факты опирается объявление.
    assert "цена услуги «Маникюр»" in view["ads"][0]["facts"]
    assert view["needs_review"] is False

    assert (await client.get(path)).status_code == 404
    assert (await client.get(path, headers={"X-Set-Token": "x" * 40})).status_code == 404


async def test_draft_without_services_is_refused(guest: Guest) -> None:  # noqa: F811
    client = guest("203.0.113.5")
    refused = await client.post("/api/creative-sets", json={**DRAFT, "services": []})
    assert refused.status_code == 422
    assert "услуг" in refused.json()["detail"]


async def test_guest_limit_per_network(guest: Guest, sessions: Sessions) -> None:  # noqa: F811
    client = guest("203.0.113.5")
    for _ in range(WIZARD.max_guest_sets_per_address):
        assert (await client.post("/api/creative-sets", json=DRAFT)).status_code == 202
    refused = await client.post("/api/creative-sets", json=DRAFT)
    assert refused.status_code == 429
    assert "завтра" in refused.json()["detail"]
    assert (await guest("198.51.100.7").post("/api/creative-sets", json=DRAFT)).status_code == 202


async def test_sensitive_niche_is_marked(guest: Guest, sessions: Sessions) -> None:  # noqa: F811
    client = guest("203.0.113.5")
    body = (await client.post("/api/creative-sets", json={**DRAFT, "niche": "косметология"})).json()
    await write(sessions)
    view = (
        await client.get(f"/api/creative-sets/{body['id']}", headers={"X-Set-Token": body["token"]})
    ).json()
    assert view["needs_review"] is True


async def test_owner_keeps_chosen_ads_after_login(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    browser: Browser,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    headers = {"X-CSRF-Token": csrf}
    base = f"/api/businesses/{business_id}"
    assert (await client.get(f"{base}/wizard-ads")).json() == {"ads": []}

    set_id, token = await written(client, sessions)
    adopt = f"{base}/creative-sets/adopt"
    for kept, words in (([], "хотя бы одно"), ([5], "хотя бы одно")):
        refused = await client.post(
            adopt, json={"id": set_id, "token": token, "kept": kept}, headers=headers
        )
        assert refused.status_code == 422, kept
        assert words in refused.json()["detail"]
    wrong = await client.post(
        adopt, json={"id": set_id, "token": "x" * 40, "kept": [0]}, headers=headers
    )
    assert wrong.status_code == 404
    no_csrf = await client.post(adopt, json={"id": set_id, "token": token, "kept": [1]})
    assert no_csrf.status_code == 403

    done = await client.post(
        adopt, json={"id": set_id, "token": token, "kept": [1]}, headers=headers
    )
    assert done.status_code == 200, done.text
    chosen = (await client.get(f"{base}/wizard-ads")).json()["ads"]
    assert [a["headline"] for a in chosen] == [SECOND["headline"]]
    # Повтор переноса после сбоя проходит.
    again = await client.post(
        adopt, json={"id": set_id, "token": token, "kept": [1]}, headers=headers
    )
    assert again.status_code == 200

    other = browser()
    await _login(other, provider, BORIS)
    assert (await other.get(f"{base}/wizard-ads")).status_code == 404


async def test_custom_ad_can_be_created_excluded_from_launch(anna: Anna) -> None:  # noqa: F811
    client, csrf, business_id = anna
    base = f"/api/businesses/{business_id}/wizard-ads"
    created = await client.post(
        base,
        json={"headline": "Своя стрижка", "text": "Запишитесь", "active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    assert created.json()["active"] is False
    assert (await client.get(base)).json()["ads"] == []
    saved = (await client.get(base, params={"include_inactive": "true"})).json()["ads"]
    assert len(saved) == 1
    assert saved[0]["active"] is False


@pytest.mark.parametrize("kind", [ChangeKind.CREATE_CAMPAIGN, ChangeKind.CREATE_AD])
async def test_wizard_ads_identify_confirmed_submissions(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    kind: ChangeKind,
    provider: FakeProvider,
) -> None:
    client, csrf, business_id = anna
    await _connect(client, provider, csrf, str(business_id), ANNA)
    url = f"/api/businesses/{business_id}/wizard-ads"
    sent = {"headline": "Уже отправлено", "text": "Запишитесь"}
    other = {"headline": "Новый черновик", "text": "Приходите"}
    headers = {"X-CSRF-Token": csrf}
    for body in (sent, other):
        assert (await client.post(url, json=body, headers=headers)).status_code == 201
    planned = {"ads": [sent]} if kind is ChangeKind.CREATE_CAMPAIGN else sent
    repo = SqlChangeRepository(sessions, business_id)
    # Ожидающая запись не выдаётся за подтверждённую отправку.
    await repo.add(
        ProposedChange("anna-direct", ChangeSource.USER, kind, "sent", planned, "Запуск", "sent"),
        {},
        ChangeStatus.APPLIED,
    )
    await repo.add(
        ProposedChange(
            "anna-direct", ChangeSource.USER, kind, "wait", {"ads": [other]}, "Ждёт", "wait"
        ),
        {},
        ChangeStatus.PLANNED,
    )
    saved = (await client.get(url)).json()["ads"]
    assert [ad["submitted"] for ad in saved] == [True, False]


async def test_custom_ad_retry_is_idempotent_even_when_first_requests_overlap(
    anna: Anna,  # noqa: F811
) -> None:
    client, csrf, business_id = anna
    headers = {"X-CSRF-Token": csrf}
    url = f"/api/businesses/{business_id}/wizard-ads"
    body = {"headline": "Своё объявление", "text": "Запишитесь", "client_id": "custom-stable"}

    # Оба запроса начинают, когда набора ещё нет; один ответ мог потеряться в сети.
    first, retry = await asyncio.gather(
        client.post(url, json=body, headers=headers),
        client.post(url, json=body, headers=headers),
    )
    assert first.status_code == retry.status_code == 201
    assert first.json()["id"] == retry.json()["id"]
    assert len((await client.get(url)).json()["ads"]) == 1

    ad_id = first.json()["id"]
    edited = {"headline": "Правка владельца", "text": "Приходите"}
    assert (await client.put(f"{url}/{ad_id}", json=edited, headers=headers)).status_code == 200
    assert (
        await client.post(f"{url}/{ad_id}/archive", headers=headers, json={})
    ).status_code == 200
    replay = await client.post(url, json=body, headers=headers)
    assert replay.json() == {**first.json(), **edited, "active": False, "archived": True}

    # Без ключа совпадающие тексты остаются отдельными объявлениями.
    for _ in range(2):
        assert (await client.post(url, json=edited, headers=headers)).status_code == 201
    assert len((await client.get(url, params={"include_inactive": True})).json()["ads"]) == 3


async def test_wizard_ads_crud_operations(
    anna: Anna,  # noqa: F811
) -> None:
    client, csrf, business_id = anna
    headers = {"X-CSRF-Token": csrf}
    base = f"/api/businesses/{business_id}"

    # Создание своего объявления
    created = await client.post(
        f"{base}/wizard-ads",
        json={"headline": "Своя стрижка", "text": "Мужские стрижки от мастера."},
        headers=headers,
    )
    assert created.status_code == 201
    ad = created.json()
    assert ad["headline"] == "Своя стрижка"
    assert ad["text"] == "Мужские стрижки от мастера."
    assert ad["active"] is True
    assert ad["archived"] is False
    ad_id = ad["id"]

    # Обновление текста
    updated = await client.put(
        f"{base}/wizard-ads/{ad_id}",
        json={"headline": "Своя стрижка люкс", "text": "Премиум уход и стрижка."},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["headline"] == "Своя стрижка люкс"

    # Отключение объявления
    state = await client.post(
        f"{base}/wizard-ads/{ad_id}/state",
        json={"active": False},
        headers=headers,
    )
    assert state.status_code == 200
    assert state.json()["active"] is False

    # Включение обратно
    state_on = await client.post(
        f"{base}/wizard-ads/{ad_id}/state",
        json={"active": True},
        headers=headers,
    )
    assert state_on.status_code == 200
    assert state_on.json()["active"] is True

    # Архивирование
    arch = await client.post(
        f"{base}/wizard-ads/{ad_id}/archive",
        json={"archived": True},
        headers=headers,
    )
    assert arch.status_code == 200
    assert arch.json()["archived"] is True
    assert arch.json()["active"] is False

    # Разархивирование
    unarch = await client.post(
        f"{base}/wizard-ads/{ad_id}/archive",
        json={"archived": False},
        headers=headers,
    )
    assert unarch.status_code == 200
    assert unarch.json()["archived"] is False
    assert unarch.json()["active"] is True

    # Удаление (мягкое в архив)
    deleted = await client.delete(
        f"{base}/wizard-ads/{ad_id}",
        headers=headers,
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "archived"

    # Проверка списка: активных объявлений нет, в полном списке есть архивное
    active_ads = (await client.get(f"{base}/wizard-ads")).json()["ads"]
    assert len(active_ads) == 0

    all_ads = (await client.get(f"{base}/wizard-ads?include_inactive=true")).json()["ads"]
    assert len(all_ads) == 1
    assert all_ads[0]["id"] == ad_id
    assert all_ads[0]["archived"] is True


async def test_wizard_ads_via_control_routes(
    anna: Anna,  # noqa: F811
) -> None:
    client, csrf, business_id = anna
    headers = {"X-CSRF-Token": csrf}
    base = f"/api/businesses/{business_id}"

    # Создаём черновик объявления
    created = await client.post(
        f"{base}/wizard-ads",
        json={"headline": "Маникюр со скидкой", "text": "Быстро и качественно."},
        headers=headers,
    )
    assert created.status_code == 201
    ad_id = created.json()["id"]

    # Проверяем остановку через /ads/draft/{ad_id}/state
    st_off = await client.post(
        f"{base}/ads/draft/{ad_id}/state",
        json={"active": False, "request_id": "r-draft-off"},
        headers=headers,
    )
    assert st_off.status_code == 200
    assert st_off.json() == {"status": "applied"}

    ads_list = (await client.get(f"{base}/wizard-ads?include_inactive=true")).json()["ads"]
    assert ads_list[0]["active"] is False

    # Включение обратно через /ads/draft/{ad_id}/state
    st_on = await client.post(
        f"{base}/ads/draft/{ad_id}/state",
        json={"active": True, "request_id": "r-draft-on"},
        headers=headers,
    )
    assert st_on.status_code == 200
    assert st_on.json() == {"status": "applied"}

    ads_list = (await client.get(f"{base}/wizard-ads?include_inactive=true")).json()["ads"]
    assert ads_list[0]["active"] is True

    # Архивирование через /ads/draft/{ad_id}/archive
    arch = await client.post(
        f"{base}/ads/draft/{ad_id}/archive",
        headers=headers,
    )
    assert arch.status_code == 200
    assert arch.json() == {"status": "applied"}

    ads_list = (await client.get(f"{base}/wizard-ads?include_inactive=true")).json()["ads"]
    assert ads_list[0]["archived"] is True
    assert ads_list[0]["active"] is False

    # Разархивирование через прямой endpoint wizard-ads (проверяем сброс off=False)
    unarch = await client.post(
        f"{base}/wizard-ads/{ad_id}/archive",
        json={"archived": False},
        headers=headers,
    )
    assert unarch.status_code == 200
    assert unarch.json()["archived"] is False
    assert unarch.json()["active"] is True

    # Проверка несуществующего индекса
    not_found = await client.post(
        f"{base}/ads/draft/999/state",
        json={"active": False, "request_id": "r-missing"},
        headers=headers,
    )
    assert not_found.status_code == 404


async def test_wizard_ads_whitespace_validation(
    anna: Anna,  # noqa: F811
) -> None:
    client, csrf, business_id = anna
    headers = {"X-CSRF-Token": csrf}
    base = f"/api/businesses/{business_id}"

    # Заголовок из пробелов
    resp1 = await client.post(
        f"{base}/wizard-ads",
        json={"headline": "   ", "text": "Нормальный текст"},
        headers=headers,
    )
    assert resp1.status_code == 422

    # Текст из пробелов
    resp2 = await client.post(
        f"{base}/wizard-ads",
        json={"headline": "Нормальный заголовок", "text": "   "},
        headers=headers,
    )
    assert resp2.status_code == 422
