"""Свои фото владельца: HTTP → проверка и обработка → база → просмотр и снятие из подбора."""

import io
import uuid
from datetime import UTC, datetime

from PIL import Image
from sqlalchemy import text

from tests.accounts.fake_provider import FakeProvider
from tests.accounts.test_flow import BORIS, Browser, _login
from tests.api.test_control import Anna, Sessions, anna, platform  # noqa: F401


def _jpeg(width: int = 800, height: int = 800, gps: bool = False) -> bytes:
    image = Image.new("RGB", (width, height), (200, 120, 140))
    output = io.BytesIO()
    exif = Image.Exif()
    if gps:
        exif[0x010F] = "PhoneMaker"
    image.save(output, format="JPEG", exif=exif.tobytes())
    return output.getvalue()


JPEG = {"Content-Type": "image/jpeg"}


async def test_owner_uploads_photo_sees_it_and_removes_it(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    base = f"/api/businesses/{business}/photos"
    raw = _jpeg(gps=True)

    assert (await client.post(f"{base}?consent=true", content=raw, headers=JPEG)).status_code == 403
    no_consent = await client.post(base, content=raw, headers={**csrf, **JPEG})
    assert no_consent.status_code == 422 and "согласны" in no_consent.json()["detail"]

    added = await client.post(f"{base}?consent=true", content=raw, headers={**csrf, **JPEG})
    assert added.status_code == 201, added.text
    listed = (await client.get(base)).json()
    assert listed["max"] == 5
    assert [p["id"] for p in listed["photos"]] == [added.json()["id"]]

    shown = await client.get(listed["photos"][0]["url"])
    assert shown.status_code == 200
    assert shown.headers["content-type"] == "image/jpeg"
    assert shown.headers["cache-control"].startswith("private")
    # Метаданные телефона не сохраняются.
    assert not Image.open(io.BytesIO(shown.content)).getexif()

    photo = added.json()["id"]
    assert (await client.post(f"{base}/{photo}/archive")).status_code == 403
    assert (await client.post(f"{base}/{photo}/archive", headers=csrf)).status_code == 200
    assert (await client.get(base)).json()["photos"] == []
    assert (await client.post(f"{base}/{photo}/archive", headers=csrf)).status_code == 404


async def test_unsuitable_photo_is_refused_in_owner_words(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    base = f"/api/businesses/{business}/photos?consent=true"

    small = await client.post(base, content=_jpeg(300, 300), headers={**csrf, **JPEG})
    assert small.status_code == 422 and "450" in small.json()["detail"]
    broken = await client.post(base, content=b"not an image", headers={**csrf, **JPEG})
    assert broken.status_code == 422 and "открыть" in broken.json()["detail"]
    pdf = await client.post(
        base, content=b"%PDF", headers={**csrf, "Content-Type": "application/pdf"}
    )
    assert pdf.status_code == 415
    heavy = await client.post(base, content=b"\0" * (15_000_001), headers={**csrf, **JPEG})
    assert heavy.status_code == 413 and "15 МБ" in heavy.json()["detail"]


async def test_sixth_photo_is_refused(anna: Anna) -> None:  # noqa: F811
    client, csrf, business = anna
    base = f"/api/businesses/{business}/photos?consent=true"
    for side in range(500, 505):
        made = await client.post(base, content=_jpeg(side, side), headers={**csrf, **JPEG})
        assert made.status_code == 201, made.text
    sixth = await client.post(base, content=_jpeg(600, 600), headers={**csrf, **JPEG})
    assert sixth.status_code == 422 and "5 фото" in sixth.json()["detail"]


async def test_foreign_photos_look_nonexistent(
    anna: Anna,  # noqa: F811
    browser: Browser,
    provider: FakeProvider,
) -> None:
    client, csrf, business = anna
    base = f"/api/businesses/{business}/photos"
    photo = (
        await client.post(f"{base}?consent=true", content=_jpeg(), headers={**csrf, **JPEG})
    ).json()["id"]
    boris = browser()
    boris_csrf = await _login(boris, provider, BORIS)

    assert (await boris.get(f"{base}/{photo}")).status_code == 404
    assert (await boris.get(base)).status_code == 404
    archive = await boris.post(f"{base}/{photo}/archive", headers={"X-CSRF-Token": boris_csrf})
    assert archive.status_code == 404
    missing = await client.get(f"/api/businesses/{uuid.uuid4()}/photos/{photo}")
    assert missing.status_code == 404
    assert (await client.get(f"{base}/{photo}")).status_code == 200


async def test_ad_list_names_the_image_the_ad_went_out_with(
    anna: Anna,  # noqa: F811
    sessions: Sessions,
) -> None:
    client, csrf, business = anna
    base = f"/api/businesses/{business}"
    photo = (
        await client.post(f"{base}/photos?consent=true", content=_jpeg(), headers={**csrf, **JPEG})
    ).json()["id"]
    async with sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO creatives (business_id, account, campaign_id, headline, text, source,"
                " auto_ok, reason, facts, sources, state, state_reason, history, ad_id, image_id,"
                " created_at, updated_at) SELECT business_id, account, campaign_id, title, text,"
                " 'template', true, 'refill', '[]', '[]', 'running', 'accepted', '[]', ad_id, :i,"
                " :n, :n FROM ads WHERE business_id = :b AND ad_id = '11'"
            ),
            {"b": business, "i": photo, "n": datetime(2026, 11, 10, tzinfo=UTC)},
        )

    lines = (await client.get(f"{base}/ads")).json()

    assert {line["ad_id"]: line["image_id"] for line in lines} == {"11": photo, "12": None}
