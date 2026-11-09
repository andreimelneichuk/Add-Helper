from datetime import UTC, datetime

from adhelper.campaigns.domain.keywords import AUTOTARGETING_KEYWORD
from adhelper.campaigns.domain.launch import NewAd, NewCampaign, Placement
from adhelper.platforms.adapters.direct.platform import DirectPlatform
from adhelper.platforms.adapters.direct.transport import DirectHttpClient
from tests.platforms.direct.fake_direct import FakeDirect

ACCOUNT = "test-account"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
RUB = 1_000_000


async def _no_sleep(_: float) -> None:
    return None


async def _token(account: str) -> str:
    return f"token-{account}"


def _setup() -> tuple[DirectPlatform, FakeDirect]:
    fake = FakeDirect()
    client = DirectHttpClient(transport=fake.transport(), sleep=_no_sleep)
    platform = DirectPlatform(client, _token, lambda: NOW)
    return platform, fake


async def test_keywords_add_payload_and_autotargeting() -> None:
    platform, fake = _setup()
    fake.groups[1] = {"Id": 1, "CampaignId": 10, "Name": "Группа 1", "RegionIds": [213]}

    keywords = [
        AUTOTARGETING_KEYWORD,
        "маникюр пермь",
        "педикюр цена пермь",
    ]
    added = await platform._add_keywords(ACCOUNT, 1, keywords)
    assert len(added) == 3

    # Проверяем, как сформировались запросы
    add_calls = [r for r in fake.requests if r["name"] == "keywords.add"]
    assert len(add_calls) == 1
    items = add_calls[0]["body"]["params"]["Keywords"]
    assert len(items) == 3

    # Автотаргетинг имеет нужные категории
    auto = next(item for item in items if item["Keyword"] == AUTOTARGETING_KEYWORD)
    assert auto["AdGroupId"] == 1
    assert auto["AutotargetingCategories"] == [
        {"Category": "EXACT", "Value": "YES"},
        {"Category": "ALTERNATIVE", "Value": "YES"},
        {"Category": "ACCESSORY", "Value": "YES"},
    ]

    # Обычные фразы не имеют категорий автотаргетинга
    normal = next(item for item in items if item["Keyword"] == "маникюр пермь")
    assert "AutotargetingCategories" not in normal


async def test_keywords_add_idempotency() -> None:
    platform, fake = _setup()
    fake.groups[1] = {"Id": 1, "CampaignId": 10, "Name": "Группа 1", "RegionIds": [213]}

    keywords = ["маникюр пермь", "педикюр пермь"]
    # Первый вызов добавляет 2 фразы
    added1 = await platform._add_keywords(ACCOUNT, 1, keywords)
    assert len(added1) == 2
    assert len(fake.keywords) == 2

    # Повторный вызов видит существующие фразы через keywords.get и ничего не добавляет
    added2 = await platform._add_keywords(ACCOUNT, 1, keywords)
    assert added2 == []
    assert len(fake.keywords) == 2

    # Вызов с одной новой фразой добавляет только её
    added3 = await platform._add_keywords(ACCOUNT, 1, ["маникюр пермь", "дизайн ногтей пермь"])
    assert len(added3) == 1
    assert len(fake.keywords) == 3
    assert any(kw["Keyword"] == "дизайн ногтей пермь" for kw in fake.keywords.values())


async def test_keywords_add_respects_group_limit_of_200() -> None:
    platform, fake = _setup()
    fake.groups[1] = {"Id": 1, "CampaignId": 10, "Name": "Группа 1", "RegionIds": [213]}

    # Директ ограничивает группу 200 фразами, даже если план содержит больше.
    lots_of_keywords = [f"услуга номер {i} пермь" for i in range(250)]

    added = await platform._add_keywords(ACCOUNT, 1, lots_of_keywords)
    assert len(added) == 200
    assert len(fake.keywords) == 200

    # Проверяем, что в запрос ушёл только допустимый объём.
    add_calls = [r for r in fake.requests if r["name"] == "keywords.add"]
    assert len(add_calls) == 1
    assert len(add_calls[0]["body"]["params"]["Keywords"]) == 200


async def test_keywords_add_uses_only_remaining_group_slots() -> None:
    platform, fake = _setup()
    fake.groups[1] = {"Id": 1, "CampaignId": 10, "Name": "Группа 1", "RegionIds": [213]}
    fake.keywords = {
        i: {"Id": i, "AdGroupId": 1, "Keyword": f"существующая фраза {i}"}
        for i in range(1_000, 1_195)
    }

    added = await platform._add_keywords(
        ACCOUNT, 1, [f"новая фраза {i}" for i in range(20)]
    )

    assert len(added) == 5
    assert len(fake.keywords) == 200


async def test_create_campaign_adds_keywords() -> None:
    platform, fake = _setup()
    campaign = NewCampaign(
        marker="ah-kw-test-1",
        name="Кампания с фразами (ah-kw-test-1)",
        placement=Placement.SEARCH,
        limit=3000 * RUB,
        ends_at=datetime(2026, 12, 1, 0, 0, tzinfo=UTC),
        regions=(213,),
        href="https://example.com/",
        counters=(),
        ads=(NewAd("Заголовок", "Текст объявления"),),
        keywords=(AUTOTARGETING_KEYWORD, "стрижка москва", "окрашивание москва"),
    )

    await platform.create_campaign(ACCOUNT, campaign)

    # Кампания и группа созданы
    assert len(fake.campaigns) == 1
    assert len(fake.groups) == 1
    # Фразы добавлены в созданную группу
    group_id = next(iter(fake.groups.keys()))
    group_keywords = [kw for kw in fake.keywords.values() if kw["AdGroupId"] == group_id]
    assert len(group_keywords) == 3
    assert {kw["Keyword"] for kw in group_keywords} == {
        AUTOTARGETING_KEYWORD,
        "стрижка москва",
        "окрашивание москва",
    }
