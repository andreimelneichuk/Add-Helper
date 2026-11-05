"""Объявления в мастере до входа: набор писателя по фактам черновика, потом выбор в бизнес."""

import uuid

import pytest

from adhelper.creatives.adapters.rules_file import load_wizard_rules
from adhelper.creatives.adapters.sql import SqlCreatives
from adhelper.creatives.adapters.wizard import (
    SqlWizardAds,
    SqlWizardSets,
    TooManyGuestSets,
    TooManySets,
)
from adhelper.creatives.application.service import CURRENT_RULES
from adhelper.creatives.application.wizard import CURRENT_WIZARD_RULES
from adhelper.creatives.domain.model import (
    BusinessProfile,
    CreativeSource,
    Service,
    SetStatus,
)
from tests.creatives.test_service import (
    GOOD,
    INVENTED,
    NOW,
    SECOND,
    FakeModel,
    Sessions,
    _run,
    _sets,
    ads,
)
from tests.seed import seed_account

WIZARD = load_wizard_rules(CURRENT_WIZARD_RULES)
DRAFT = BusinessProfile(
    "", (Service("Маникюр", 1500),), "ул. Ленина, 5", ("Стерильные инструменты",), "маникюр"
)
HERE = "net-a"


async def _request(sessions: Sessions, key: str = HERE, token: str = "t1") -> int:
    return await SqlWizardSets(sessions).request(DRAFT, key, token, WIZARD, CURRENT_RULES, NOW)


async def test_guest_set_is_written_from_draft_facts(sessions: Sessions) -> None:
    set_id = await _request(sessions)
    sets = SqlWizardSets(sessions)
    waiting = await sets.get(set_id, "t1")
    assert waiting is not None and waiting.status is SetStatus.QUEUED

    model = FakeModel(ads(GOOD, SECOND, INVENTED))
    assert await _run(sessions, model) == 1
    # Писатель видит факты черновика: у гостя нет профиля в базе.
    assert "Стерильные инструменты" in model.prompts[0]

    ready = await sets.get(set_id, "t1")
    assert ready is not None and ready.status is SetStatus.READY
    assert ready.source is CreativeSource.MODEL
    assert [d.headline for d in ready.drafts] == [GOOD["headline"], SECOND["headline"]]
    # У каждого объявления видно, на какие факты оно опирается.
    assert {s.key for s in ready.drafts[0].sources} >= {"price:Маникюр:1500"}
    # Строк объявлений нет: направлений до запуска не существует.
    assert await _creatives_count(sessions) == 0


async def test_without_model_set_falls_back_to_templates(sessions: Sessions) -> None:
    set_id = await _request(sessions)
    await _run(sessions, None)
    ready = await SqlWizardSets(sessions).get(set_id, "t1")
    assert ready is not None and ready.source is CreativeSource.TEMPLATE
    assert ready.drafts


async def test_other_token_sees_nothing(sessions: Sessions) -> None:
    set_id = await _request(sessions)
    assert await SqlWizardSets(sessions).get(set_id, "t2") is None


async def test_guest_limits_per_network_and_overall(sessions: Sessions) -> None:
    for n in range(WIZARD.max_guest_sets_per_address):
        await _request(sessions, token=f"a{n}")
    with pytest.raises(TooManySets):
        await _request(sessions, token="over")
    await _request(sessions, key="net-b", token="b0")

    tight = WIZARD.__class__(**{**WIZARD.__dict__, "max_guest_sets_per_day": 1})
    with pytest.raises(TooManyGuestSets):
        await SqlWizardSets(sessions).request(DRAFT, "net-c", "c0", tight, CURRENT_RULES, NOW)


async def test_choice_moves_into_business_once(sessions: Sessions) -> None:
    anna, boris = uuid.uuid4(), uuid.uuid4()
    await seed_account(sessions, anna)
    await seed_account(sessions, boris)
    set_id = await _request(sessions)
    await _run(sessions, FakeModel(ads(GOOD, SECOND)))
    sets = SqlWizardSets(sessions)

    assert not await sets.adopt(set_id, "wrong", anna, (1,))
    assert await sets.adopt(set_id, "t1", anna, (1,))
    # Повтор после сбоя переноса безопасен, а чужой бизнес набор не заберёт.
    assert await sets.adopt(set_id, "t1", anna, (1,))
    assert not await sets.adopt(set_id, "t1", boris, (0,))
    # Ключ гостя больше ничего не показывает: набор принадлежит бизнесу.
    assert await sets.get(set_id, "t1") is None

    chosen = await SqlWizardAds(sessions, anna).chosen()
    assert [d.headline for d in chosen] == [SECOND["headline"]]
    assert await SqlWizardAds(sessions, boris).chosen() == ()
    # Набор мастера не мешает экрану объявлений и не съедает места направлений.
    assert (await SqlCreatives(sessions, anna).writing()) == []
    rows = await _sets(sessions)
    assert rows[0]["business_id"] == anna and rows[0]["guest_key"] is None


async def test_unfinished_set_is_not_adopted(sessions: Sessions) -> None:
    business = uuid.uuid4()
    await seed_account(sessions, business)
    set_id = await _request(sessions)
    assert not await SqlWizardSets(sessions).adopt(set_id, "t1", business, (0,))


async def test_repeated_request_with_same_profile_returns_cached_ready_set(
    sessions: Sessions,
) -> None:
    set_id = await _request(sessions, token="orig-token")
    await _run(sessions, FakeModel(ads(GOOD)))
    sets = SqlWizardSets(sessions)
    ready = await sets.get(set_id, "orig-token")
    assert ready is not None and ready.status == SetStatus.READY

    # Повторный запрос с тем же профилем возвращает готовый кэшированный id
    cached_id = await SqlWizardSets(sessions).request(
        DRAFT, "net-a", "new-token", WIZARD, CURRENT_RULES, NOW
    )
    assert cached_id == set_id
    cached = await sets.get(cached_id, "new-token")
    assert cached is not None and cached.status == SetStatus.READY



async def _creatives_count(sessions: Sessions) -> int:
    from sqlalchemy import text

    async with sessions() as session:
        return int(await session.scalar(text("SELECT count(*) FROM creatives")) or 0)
