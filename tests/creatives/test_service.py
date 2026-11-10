"""Хранитель мест и очередь наборов в настоящем PostgreSQL, модель поддельная."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.creatives.adapters.rules_file import load_rules, load_slot_rules
from adhelper.creatives.adapters.sql import (
    SqlAdSlots,
    SqlCreativeQueue,
    SqlCreatives,
    SqlKeeperBusinesses,
    SqlProfiles,
    SqlProfileSource,
)
from adhelper.creatives.application.service import (
    CURRENT_RULES,
    CURRENT_SLOT_RULES,
    MAX_ATTEMPTS,
    STALE_AFTER,
    WrongState,
    board,
    confirm,
    creative_report,
    keep_ads,
    run_queued,
    withdraw,
)
from adhelper.creatives.domain.lifecycle import CreativeState
from adhelper.creatives.domain.model import (
    BusinessProfile,
    CreativeSource,
    ModelReply,
    Service,
    SetStatus,
)
from tests.claims import with_claims
from tests.seed import seed_account

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
RULES = load_rules(CURRENT_RULES)
SLOTS = load_slot_rules(CURRENT_SLOT_RULES)
PROFILE = BusinessProfile(
    "Тест", (Service("Маникюр", 1500),), "ул. Ленина, 5", ("Стерильные инструменты",), "маникюр"
)
GOOD = {"headline": "Маникюр за 1500 ₽", "text": "Стерильные инструменты, ул. Ленина, 5."}
SECOND = {"headline": "Маникюр рядом с домом", "text": "Запишитесь: ул. Ленина, 5."}
INVENTED = {"headline": "Маникюр за 500 ₽", "text": "Только сегодня скидка!"}
Sessions = async_sessionmaker[AsyncSession]


class FakeModel:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls = 0
        self.prompts: list[str] = []

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        self.calls += 1
        self.prompts.append(user)
        if isinstance(self.reply, Exception):
            raise self.reply
        return ModelReply(with_claims(self.reply, user), 100)


class FakeAttention:
    def __init__(self) -> None:
        self.open: set[str] = set()

    async def raise_alert(self, kind: str, details: object) -> None:
        self.open.add(kind)

    async def resolve(self, kind: str) -> None:
        self.open.discard(kind)


def ads(*items: dict[str, str]) -> str:
    return json.dumps({"ads": list(items)}, ensure_ascii=False)


async def _campaign(
    sessions: Sessions, business_id: uuid.UUID, campaign_id: str = "1", state: str = "on"
) -> None:
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO campaigns (business_id, account, campaign_id, name, state, targeting)"
                " VALUES (:b, 'lak', :c, 'Маникюр — поиск', :s,"
                ' \'{"placement": "search", "regions": ["Казань"]}\')'
            ),
            {"b": business_id, "c": campaign_id, "s": state},
        )


@pytest.fixture
async def business(sessions: Sessions) -> uuid.UUID:
    business_id = uuid.uuid4()
    await seed_account(sessions, business_id)
    await SqlProfiles(sessions, business_id).save(PROFILE, "https://lak.example")
    await _campaign(sessions, business_id)
    return business_id


async def _keep(
    sessions: Sessions,
    business_id: uuid.UUID,
    now: datetime = NOW,
    attention: FakeAttention | None = None,
    owner_decides: bool = False,
) -> None:
    await keep_ads(
        business_id,
        SqlAdSlots(sessions, business_id),
        SqlProfileSource(sessions),
        attention or FakeAttention(),
        SLOTS,
        RULES,
        now,
        owner_decides=owner_decides,
    )


async def _run(sessions: Sessions, model: FakeModel | None, now: datetime = NOW) -> int:
    return await run_queued(
        SqlCreativeQueue(sessions), SqlProfileSource(sessions), model, None, load_rules, lambda: now
    )


async def _sets(sessions: Sessions) -> list[dict[str, object]]:
    async with sessions() as session:
        rows = await session.execute(text("SELECT * FROM creative_sets ORDER BY id"))
        return [dict(row._mapping) for row in rows]


async def test_profile_round_trip_takes_name_from_business(
    sessions: Sessions, business: uuid.UUID
) -> None:
    assert await SqlProfiles(sessions, business).get() == (PROFILE, "https://lak.example")
    assert await SqlProfiles(sessions, uuid.uuid4()).get() is None


async def test_new_campaign_is_written_for_and_ads_take_places(
    sessions: Sessions, business: uuid.UUID
) -> None:
    assert await SqlKeeperBusinesses(sessions).business_ids() == [business]
    await _keep(sessions, business)
    queued = await _sets(sessions)
    assert [(s["account"], s["campaign_id"], s["reason"], s["wanted"]) for s in queued] == [
        ("lak", "1", "new_campaign", 3)
    ]
    # Повтор цикла не заказывает второй набор, пока первый пишется.
    await _keep(sessions, business, NOW + timedelta(minutes=5))
    assert len(await _sets(sessions)) == 1

    model = FakeModel(ads(GOOD, SECOND, INVENTED))
    assert await _run(sessions, model) == 1
    assert "Маникюр — поиск" in model.prompts[0]
    stored = (await _sets(sessions))[0]
    assert stored["status"] == SetStatus.READY.value
    assert stored["source"] == CreativeSource.MODEL.value

    creatives = SqlCreatives(sessions, business)
    written = await creatives.all()
    # Выдуманная цена не прошла проверку и в объявления не попала.
    assert {c.headline for c in written} == {GOOD["headline"], SECOND["headline"]}
    assert {c.state for c in written} == {CreativeState.READY}
    assert {c.reason for c in written} == {"new_campaign"}

    await _keep(sessions, business, NOW + timedelta(minutes=10))
    # Без судьи текст модели без владельца не запускается.
    placed = await creatives.all()
    assert {c.state for c in placed} == {CreativeState.NEEDS_OWNER}
    # Третье место свободно: дописываем один текст.
    assert [(s["reason"], s["wanted"]) for s in (await _sets(sessions))[1:]] == [("refill", 1)]


@pytest.mark.parametrize(
    ("owner_decides", "state"),
    [(True, CreativeState.NEEDS_OWNER), (False, CreativeState.LAUNCH_PENDING)],
)
async def test_checked_ad_takes_place_on_its_own_only_when_autopilot_acts_alone(
    sessions: Sessions, business: uuid.UUID, owner_decides: bool, state: CreativeState
) -> None:
    """В «Следит» и «Предлагает» объявление ждёт владельца, в «Сам» уходит в очередь отправки."""
    await _keep(sessions, business, owner_decides=owner_decides)
    await _run(sessions, None)
    attention = FakeAttention()
    await _keep(
        sessions, business, NOW + timedelta(minutes=5), attention, owner_decides=owner_decides
    )
    placed = await SqlCreatives(sessions, business).all()
    assert placed and {c.state for c in placed} >= {state}
    assert ("ads_need_confirmation" in attention.open) is owner_decides


async def test_owner_launches_or_withdraws_and_report_shows_basis(
    sessions: Sessions, business: uuid.UUID
) -> None:
    await _keep(sessions, business)
    await _run(sessions, FakeModel(ads(GOOD, SECOND)))
    attention = FakeAttention()
    await _keep(sessions, business, NOW + timedelta(minutes=5), attention)
    assert attention.open == {"ads_need_confirmation"}

    creatives = SqlCreatives(sessions, business)
    first, second = sorted(await creatives.all(), key=lambda c: c.id)
    await confirm(creatives, first.id, NOW)
    await withdraw(creatives, second.id, NOW)
    with pytest.raises(WrongState):
        await confirm(creatives, second.id, NOW)

    report = await creative_report(creatives, PROFILE, first.id)
    assert report is not None
    assert report.creative.state is CreativeState.LAUNCH_PENDING
    assert [h.reason for h in report.creative.history] == [
        "written",
        "place_free",
        "owner_confirmed",
    ]
    assert report.campaign_name == "Маникюр — поиск"
    assert report.audience.regions == ("Казань",)
    assert "цена услуги «Маникюр»" in {s.label for s in report.basis.sources}
    assert report.basis.unconfirmed == ()

    await _keep(sessions, business, NOW + timedelta(minutes=10), attention)
    assert attention.open == set()


async def test_changed_price_stops_waiting_ad(sessions: Sessions, business: uuid.UUID) -> None:
    await _keep(sessions, business)
    await _run(sessions, FakeModel(ads(GOOD, SECOND)))
    await SqlProfiles(sessions, business).save(
        BusinessProfile(
            "", (Service("Маникюр", 1700),), PROFILE.address, PROFILE.features, "маникюр"
        ),
        "",
    )
    await _keep(sessions, business, NOW + timedelta(minutes=5))
    states = {
        c.headline: (c.state, c.state_reason) for c in await SqlCreatives(sessions, business).all()
    }
    assert states[GOOD["headline"]] == (CreativeState.STOPPED, "fact_changed")
    reasons = [s["reason"] for s in await _sets(sessions)]
    assert reasons[-1] == "fact_changed"


async def test_daily_limit_stops_ordering(sessions: Sessions, business: uuid.UUID) -> None:
    for minute in range(RULES.max_sets_per_day):
        await _campaign(sessions, business, f"c{minute}")
    await _campaign(sessions, business, "late")
    await _keep(sessions, business)
    # Шесть направлений, а наборов в сутки пять: шестое ждёт.
    assert len(await _sets(sessions)) == RULES.max_sets_per_day


async def test_paused_campaign_and_other_business_are_left_alone(
    sessions: Sessions, business: uuid.UUID
) -> None:
    other = uuid.uuid4()
    await seed_account(sessions, other)
    await _campaign(sessions, other, state="suspended")
    await _keep(sessions, other)
    assert await _sets(sessions) == []

    await _keep(sessions, business)
    await _run(sessions, FakeModel(ads(GOOD)))
    mine = (await SqlCreatives(sessions, business).all())[0]
    strangers = SqlCreatives(sessions, other)
    assert await strangers.all() == []
    assert await strangers.get(mine.id) is None
    assert (await board(strangers)).writing == []
    with pytest.raises(WrongState):
        await withdraw(strangers, mine.id, NOW)
    assert (await SqlCreatives(sessions, business).get(mine.id)) == mine


async def test_without_profile_nothing_is_ordered_and_owner_is_asked(sessions: Sessions) -> None:
    business_id = uuid.uuid4()
    await seed_account(sessions, business_id)
    await _campaign(sessions, business_id)
    attention = FakeAttention()
    await _keep(sessions, business_id, attention=attention)
    assert await _sets(sessions) == []
    assert attention.open == {"ads_no_profile"}


async def test_crash_is_recorded_by_type_only(
    sessions: Sessions, business: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    await _keep(sessions, business)

    await _run(sessions, FakeModel(RuntimeError("sk-secret ответ модели")))

    stored = (await _sets(sessions))[0]
    assert (stored["status"], stored["notes"]) == (SetStatus.FAILED.value, ["RuntimeError"])
    assert "sk-secret" not in caplog.text


async def test_profile_emptied_after_order_fails_without_model_call(
    sessions: Sessions, business: uuid.UUID
) -> None:
    await _keep(sessions, business)
    await SqlProfiles(sessions, business).save(BusinessProfile("", ()), "")
    model = FakeModel(ads(GOOD))

    assert await _run(sessions, model) == 0

    stored = (await _sets(sessions))[0]
    assert (stored["status"], stored["notes"]) == (SetStatus.FAILED.value, ["no_profile"])
    assert model.calls == 0


async def test_stale_running_set_is_retried_until_attempts_run_out(
    sessions: Sessions, business: uuid.UUID
) -> None:
    await _keep(sessions, business)
    queue = SqlCreativeQueue(sessions)
    # Воркер взял набор и упал: строка осталась «в работе».
    for attempt in range(MAX_ATTEMPTS):
        moment = NOW + attempt * (STALE_AFTER + timedelta(minutes=1))
        assert await queue.claim(moment, moment - STALE_AFTER, MAX_ATTEMPTS) is not None
        fresh = await queue.claim(moment, moment - STALE_AFTER, MAX_ATTEMPTS)
        assert fresh is None, "свежий набор в работе не берётся второй раз"

    later = NOW + MAX_ATTEMPTS * (STALE_AFTER + timedelta(minutes=1))
    assert await _run(sessions, FakeModel(ads(GOOD)), later) == 0
    stored = (await _sets(sessions))[0]
    assert (stored["status"], stored["notes"]) == (SetStatus.FAILED.value, ["stale"])
    assert await SqlCreatives(sessions, business).all() == []


async def test_late_fail_does_not_overwrite_ready_set(
    sessions: Sessions, business: uuid.UUID
) -> None:
    await _keep(sessions, business)
    await _run(sessions, FakeModel(ads(GOOD)))
    set_id = int(str((await _sets(sessions))[0]["id"]))

    await SqlCreativeQueue(sessions).fail(set_id, "late_duplicate", NOW)

    assert (await _sets(sessions))[0]["status"] == SetStatus.READY.value
    assert len(await SqlCreatives(sessions, business).all()) == 1


async def test_concurrent_workers_take_different_sets(
    sessions: Sessions, business: uuid.UUID
) -> None:
    await _campaign(sessions, business, "2")
    await _keep(sessions, business)
    queue = SqlCreativeQueue(sessions)
    first = await queue.claim(NOW, NOW - STALE_AFTER, MAX_ATTEMPTS)
    second = await queue.claim(NOW, NOW - STALE_AFTER, MAX_ATTEMPTS)
    assert first is not None and second is not None
    assert {first.campaign_id, second.campaign_id} == {"1", "2"}
    assert {s["attempts"] for s in await _sets(sessions)} == {1}
