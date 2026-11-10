"""Фото для объявления: сделать по услуге из профиля и отправить объявление с выбранным фото."""

import io

import httpx
from PIL import Image
from sqlalchemy import text

from adhelper.creatives.domain.images import GeneratedImage
from adhelper.ledger.adapters.sql import SqlChangeRepository
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus, ProposedChange
from tests.api.test_control import Anna, Sessions, _app, anna, platform  # noqa: F401
from tests.api.test_photos import JPEG, _jpeg
from tests.api.test_wizard_ads import written
from tests.budget.fake_platform import FakePlatform

PROFILE = {
    "services": [{"name": "Маникюр", "price": 1500}],
    "address": "ул. Ленина, 5",
    "features": ["Стерильные инструменты"],
    "niche": "маникюр",
    "website": "https://lak.example",
}


class FakeImageModel:
    name = "fake-image"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(self, prompt: str, aspect_ratio: str) -> GeneratedImage:
        self.calls.append(prompt)
        out = io.BytesIO()
        Image.new("RGB", (900, 900), (len(self.calls) * 40, 90, 160)).save(out, format="PNG")
        return GeneratedImage(out.getvalue(), "image/png", 30_000)


async def _profile(client: httpx.AsyncClient, base: str, csrf: dict[str, str]) -> None:
    assert (await client.put(f"{base}/profile", json=PROFILE, headers=csrf)).status_code == 200


async def test_generated_photo_is_made_for_profile_service_and_listed(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    base = f"/api/businesses/{business}"
    model = FakeImageModel()
    _app(client).state.image_models = (model,)
    _app(client).state.text_model = None
    await _profile(client, base, csrf)

    assert (
        await client.post(f"{base}/photos/generate", json={"service": "Маникюр"})
    ).status_code == 403
    foreign = await client.post(
        f"{base}/photos/generate", json={"service": "Бурение"}, headers=csrf
    )
    assert foreign.status_code == 422 and not model.calls

    made = await client.post(f"{base}/photos/generate", json={"service": "Маникюр"}, headers=csrf)
    assert made.status_code == 201, made.text
    assert made.json()["source"] == "generated"
    assert len(model.calls) == 1

    # Свои фото и сделанные сервисом лежат раздельно: автоподбор берёт только свои.
    assert (await client.get(f"{base}/photos")).json()["photos"] == []
    listed = (await client.get(f"{base}/photos", params={"include_generated": "true"})).json()
    assert [(p["id"], p["source"]) for p in listed["photos"]] == [(made.json()["id"], "generated")]
    shown = await client.get(listed["photos"][0]["url"])
    assert shown.status_code == 200 and shown.headers["content-type"] == "image/jpeg"


async def test_generation_stops_at_daily_limit_and_without_generator(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    base = f"/api/businesses/{business}"
    await _profile(client, base, csrf)
    _app(client).state.text_model = None

    _app(client).state.image_models = ()
    off = await client.post(f"{base}/photos/generate", json={"service": "Маникюр"}, headers=csrf)
    assert off.status_code == 503 and "выключена" in off.json()["detail"]

    model = FakeImageModel()
    _app(client).state.image_models = (model,)
    for _ in range(3):
        ok = await client.post(f"{base}/photos/generate", json={"service": "Маникюр"}, headers=csrf)
        assert ok.status_code == 201, ok.text
    limit = await client.post(f"{base}/photos/generate", json={"service": "Маникюр"}, headers=csrf)
    assert limit.status_code == 429 and "3" in limit.json()["detail"]
    assert len(model.calls) == 3


async def test_wizard_ad_goes_out_with_chosen_photo(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    platform: FakePlatform,  # noqa: F811
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda *_: platform
    base = f"/api/businesses/{business}"
    await _profile(client, base, csrf)
    photo = (
        await client.post(f"{base}/photos?consent=true", content=_jpeg(), headers={**csrf, **JPEG})
    ).json()["id"]
    ad = (
        await client.post(
            f"{base}/wizard-ads",
            json={"headline": "Маникюр 1500 ₽", "text": "Запишитесь сегодня"},
            headers=csrf,
        )
    ).json()
    repo = SqlChangeRepository(sessions, business)
    campaign = await repo.add(
        ProposedChange(
            "anna-direct",
            ChangeSource.USER,
            ChangeKind.CREATE_CAMPAIGN,
            "launch:t",
            {"href": "https://lak.example"},
            "Запуск",
            "launch-t",
        ),
        {},
        ChangeStatus.PLANNED,
    )
    await repo.finish(campaign, ChangeStatus.APPLIED, {"campaign_id": "1"}, None)

    missing = await client.post(
        f"{base}/wizard-ads/{ad['id']}/publish", json={"image_id": photo + 99}, headers=csrf
    )
    assert missing.status_code == 404

    sent = await client.post(
        f"{base}/wizard-ads/{ad['id']}/publish",
        json={"grouping": "general", "image_id": photo},
        headers=csrf,
    )
    assert sent.status_code == 200, sent.text
    async with sessions() as session:
        planned = (
            await session.execute(
                text("SELECT planned FROM changes WHERE kind = 'create_ad' AND business_id = :b"),
                {"b": business},
            )
        ).scalar_one()
    assert planned["image"] and len(planned["image"]) == 64
    assert planned["headline"] == "Маникюр 1500 ₽"


async def _adopt(
    client: httpx.AsyncClient,
    sessions: Sessions,
    csrf: dict[str, str],
    base: str,
    photos: dict[int, int],
):  # type: ignore[no-untyped-def]
    set_id, token = await written(client, sessions)
    body = {"id": set_id, "token": token, "kept": [0, 1], "photos": photos}
    return await client.post(f"{base}/creative-sets/adopt", json=body, headers=csrf)


async def test_adopted_drafts_remember_their_photos(anna: Anna, sessions: Sessions) -> None:  # noqa: F811
    client, csrf, business = anna
    base = f"/api/businesses/{business}"
    photo = (
        await client.post(f"{base}/photos?consent=true", content=_jpeg(), headers={**csrf, **JPEG})
    ).json()["id"]

    done = await _adopt(client, sessions, csrf, base, {0: photo})
    assert done.status_code == 200, done.text

    ads = (await client.get(f"{base}/wizard-ads")).json()["ads"]
    assert [a["image_id"] for a in ads] == [photo, None]


async def test_adopt_refuses_a_photo_of_another_business_or_a_draft_not_kept(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
) -> None:
    client, csrf, business = anna
    base = f"/api/businesses/{business}"
    unknown = await _adopt(client, sessions, csrf, base, {0: 999_999})
    assert unknown.status_code == 422 and "фото" in unknown.json()["detail"].lower()

    photo = (
        await client.post(f"{base}/photos?consent=true", content=_jpeg(), headers={**csrf, **JPEG})
    ).json()["id"]
    not_kept = await _adopt(client, sessions, csrf, base, {7: photo})
    assert not_kept.status_code == 422
    assert (await client.get(f"{base}/wizard-ads")).json() == {"ads": []}


async def test_publish_uses_the_draft_photo_unless_owner_says_otherwise(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
    platform: FakePlatform,  # noqa: F811
) -> None:
    client, csrf, business = anna
    _app(client).state.platform_for = lambda *_: platform
    base = f"/api/businesses/{business}"
    photo = (
        await client.post(f"{base}/photos?consent=true", content=_jpeg(), headers={**csrf, **JPEG})
    ).json()["id"]
    assert (await _adopt(client, sessions, csrf, base, {0: photo, 1: photo})).status_code == 200
    repo = SqlChangeRepository(sessions, business)
    campaign = await repo.add(
        ProposedChange(
            "anna-direct",
            ChangeSource.USER,
            ChangeKind.CREATE_CAMPAIGN,
            "launch:t",
            {"href": "https://lak.example"},
            "Запуск",
            "launch-t",
        ),
        {},
        ChangeStatus.PLANNED,
    )
    await repo.finish(campaign, ChangeStatus.APPLIED, {"campaign_id": "1"}, None)

    by_draft = await client.post(f"{base}/wizard-ads/w0/publish", json={}, headers=csrf)
    without = await client.post(
        f"{base}/wizard-ads/w1/publish", json={"image_id": None}, headers=csrf
    )
    assert by_draft.status_code == 200 and without.status_code == 200
    async with sessions() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT planned->>'headline' AS headline, planned->>'image' AS image "
                    "FROM changes WHERE kind = 'create_ad' AND business_id = :b ORDER BY id"
                ),
                {"b": business},
            )
        ).all()
    assert [bool(r.image) for r in rows] == [True, False]


def _plain(color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (800, 800), color).save(out, format="JPEG")
    return out.getvalue()


class ColorLabeler:
    """Поддельный «взгляд»: красное фото — маникюр, синее — стрижка, зелёное — не годится."""

    def __init__(self) -> None:
        self.services_seen: list[list[str]] = []

    async def subject(self, image: bytes, services):  # type: ignore[no-untyped-def]
        self.services_seen.append(list(services))
        r, _, b = Image.open(io.BytesIO(image)).convert("RGB").getpixel((5, 5))  # type: ignore[misc]
        if r > 150:
            return "Маникюр"
        return "Стрижка" if b > 150 else "unfit"


async def test_each_draft_gets_the_photo_that_fits_its_service(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    base = f"/api/businesses/{business}"
    profile = {
        **PROFILE,
        "services": [{"name": "Маникюр", "price": 1500}, {"name": "Стрижка", "price": 900}],
    }
    assert (await client.put(f"{base}/profile", json=profile, headers=csrf)).status_code == 200
    ids = {}
    for name, color in (("nails", (230, 40, 40)), ("hair", (30, 30, 230)), ("junk", (20, 200, 20))):
        added = await client.post(
            f"{base}/photos?consent=true", content=_plain(color), headers={**csrf, **JPEG}
        )
        ids[name] = added.json()["id"]
    labeler = ColorLabeler()
    _app(client).state.photo_labeler = labeler

    drafts = [{"services": ["Стрижка"]}, {"services": ["Маникюр"]}, {"services": ["Педикюр"]}]
    found = await client.post(f"{base}/photos/match", json={"drafts": drafts}, headers=csrf)

    assert found.status_code == 200, found.text
    body = found.json()
    # Педикюра нет в профиле: его слово до модели не доходит, так что фото берётся общее или пусто.
    assert [m["image_id"] for m in body["matches"]] == [ids["hair"], ids["nails"], None]
    assert body["matches"][2]["missing"] is None
    assert all(set(seen) <= {"Маникюр", "Стрижка"} for seen in labeler.services_seen)
    assert {p["id"] for p in body["photos"]} == set(ids.values())


async def test_without_a_vision_model_nothing_is_guessed(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    base = f"/api/businesses/{business}"
    await _profile(client, base, csrf)
    await client.post(
        f"{base}/photos?consent=true", content=_plain((230, 40, 40)), headers={**csrf, **JPEG}
    )
    _app(client).state.photo_labeler = None

    found = await client.post(
        f"{base}/photos/match", json={"drafts": [{"services": ["Маникюр"]}]}, headers=csrf
    )

    assert found.json()["matches"] == [{"image_id": None, "missing": "Маникюр"}]
