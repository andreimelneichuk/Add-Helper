import base64
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from adhelper.budget.adapters.ledgered import PlatformChangeGateway
from adhelper.budget.domain.limits import LimitChange
from adhelper.budget.domain.ports import PlatformUnavailable
from adhelper.campaigns.domain.launch import NewAd, NewCampaign, Placement
from adhelper.campaigns.domain.ports import Targeting
from adhelper.campaigns.domain.targeting import Schedule
from adhelper.ledger.domain.model import ChangeKind, plan_reached
from adhelper.platforms.adapters.direct.platform import (
    NO_LIMIT,
    DirectPlatform,
    _ad_view_state,
    _campaign_view_state,
    mark_href,
    parse_schedule,
    schedule_items,
    split_limit,
    strategy_of,
    unmark_href,
)
from adhelper.platforms.adapters.direct.transport import (
    DirectAuthError,
    DirectError,
    DirectHttpClient,
    DirectPointsExhausted,
    DirectRejected,
    rubles_to_micros,
)
from adhelper.platforms.adapters.gateway import RoutingGateway
from adhelper.platforms.domain.ports import AdImage, ModerationState
from tests.platforms.direct.fake_direct import FakeDirect

ACCOUNT = "andreimelneichuk"
NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
RECORDED = Path(__file__).parent / "recorded"
RUB = 1_000_000


@pytest.mark.parametrize(
    ("direct", "expected"),
    [
        ({"State": "ON", "Status": "ACCEPTED"}, "on"),
        ({"State": "ON", "Status": "MODERATION"}, "moderation"),
        ({"State": "SUSPENDED", "Status": "ACCEPTED"}, "suspended"),
        ({"State": "ARCHIVED", "Status": "MODERATION"}, "archived"),
    ],
)
def test_direct_ad_inventory_maps_state_and_moderation(
    direct: dict[str, str], expected: str
) -> None:
    assert _ad_view_state(direct) == expected


@pytest.mark.parametrize(
    ("direct", "expected"),
    [
        ({"State": "ON", "Status": "ACCEPTED"}, "on"),
        ({"State": "OFF", "Status": "MODERATION"}, "moderation"),
        ({"State": "OFF", "Status": "ACCEPTED"}, "suspended"),
        ({"State": "ARCHIVED", "Status": "MODERATION"}, "archived"),
    ],
)
def test_direct_campaign_inventory_maps_state_and_moderation(
    direct: dict[str, str], expected: str
) -> None:
    assert _campaign_view_state(direct) == expected


async def _no_sleep(_: float) -> None:
    return None


async def _token(account: str) -> str:
    return f"token-{account}"


def _platform(
    transport: httpx.AsyncBaseTransport, geo_region_cache: Any | None = None
) -> tuple[DirectPlatform, DirectHttpClient]:
    client = DirectHttpClient(transport=transport, sleep=_no_sleep)
    return DirectPlatform(client, _token, lambda: NOW, geo_region_cache=geo_region_cache), client


@pytest.fixture
def direct() -> FakeDirect:
    return FakeDirect()


@pytest.fixture
async def platform(direct: FakeDirect) -> AsyncIterator[DirectPlatform]:
    platform, client = _platform(direct.transport())
    yield platform
    await client.aclose()


async def test_city_region_uses_full_tree_and_checks_parent_ids(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.geo_regions = json.loads((RECORDED / "geo_regions_excerpt.json").read_text())

    choices = await platform.resolve_city_region_choices(ACCOUNT, "г. Пермь, ул. Ленина, 1")
    assert [choice.as_dict() for choice in choices] == [
        {
            "region_id": 50,
            "city": "Пермь",
            "display": "Пермь, Пермский край",
            "subject": "Пермский край",
        }
    ]
    assert direct.requests[0]["name"] == "dictionaries.get"
    assert direct.requests[0]["body"]["params"] == {"DictionaryNames": ["GeoRegions"]}


async def test_city_tree_is_cached_after_first_direct_fetch(direct: FakeDirect) -> None:
    class Cache:
        rows: list[dict[str, Any]] | None = None

        async def get(self) -> list[dict[str, Any]] | None:
            return self.rows

        async def put(self, regions: list[dict[str, Any]]) -> None:
            self.rows = regions

    direct.geo_regions = [
        {"GeoRegionId": 0, "GeoRegionName": "Все", "GeoRegionType": "World", "ParentId": None},
        {"GeoRegionId": 225, "GeoRegionName": "Россия", "GeoRegionType": "Country", "ParentId": 0},
        {
            "GeoRegionId": 11108,
            "GeoRegionName": "Пермский край",
            "GeoRegionType": "Federation subject",
            "ParentId": 225,
        },
        {"GeoRegionId": 50, "GeoRegionName": "Пермь", "GeoRegionType": "City", "ParentId": 11108},
    ]
    platform, client = _platform(direct.transport(), Cache())
    try:
        await platform.resolve_city_region_choices(ACCOUNT, "Пермь")
        await platform.resolve_city_region_choices(ACCOUNT, "Пермь")
    finally:
        await client.aclose()
    assert direct.calls == ["dictionaries.get"]


async def test_city_region_returns_duplicate_choices_and_address_can_disambiguate(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.geo_regions = json.loads((RECORDED / "geo_regions_excerpt.json").read_text())

    choices = await platform.resolve_city_region_choices(ACCOUNT, "Киров")
    assert {choice.region_id for choice in choices} == {46, 20200}
    narrowed = await platform.resolve_city_region_choices(
        ACCOUNT, "г. Киров, Калужская область, ул. Ленина, 1"
    )
    assert [(choice.region_id, choice.display) for choice in narrowed] == [
        (20200, "Киров, Калужская область")
    ]


async def test_city_region_ignores_city_outside_russia(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.geo_regions = [
        {"GeoRegionId": 0, "GeoRegionName": "Все", "GeoRegionType": "World", "ParentId": None},
        {
            "GeoRegionId": 1000,
            "GeoRegionName": "Россиянск",
            "GeoRegionType": "Country",
            "ParentId": 0,
        },
        {"GeoRegionId": 3, "GeoRegionName": "Тольятти", "GeoRegionType": "City", "ParentId": 1000},
    ]
    assert await platform.resolve_city_region_choices(ACCOUNT, "Тольятти") == ()


def launch(placement: Placement = Placement.SEARCH, limit: int = 2_700 * RUB) -> NewCampaign:
    return NewCampaign(
        marker="ah-k1-1",
        name="Салон «Ромашка»: поиск (ah-k1-1)",
        placement=placement,
        limit=limit,
        ends_at=datetime(2026, 12, 4, 21, 0, tzinfo=UTC),
        regions=(213,),
        href="https://romashka.example/",
        counters=(1234567,),
        ads=(
            NewAd("Стрижка у метро", "Запишитесь онлайн"),
            NewAd("Окрашивание", "Мастера с опытом"),
        ),
    )


# --- Записанные ответы настоящего кабинета (2026-11-05, кабинет без кампаний) ---


class Replay(httpx.AsyncBaseTransport):
    """Отдаёт записанные ответы по порядку и проверяет, что запросы те же."""

    def __init__(self, path: Path) -> None:
        self.entries: list[dict[str, Any]] = json.loads(path.read_text())
        self.last_adgroup_id: int | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        entry = self.entries[0]
        body = json.loads(request.content)
        body.pop("token", None)
        if (
            request.url.path.endswith("/adgroups")
            and body.get("method") == "get"
            and entry["url"].endswith("/ads")
        ):
            assert self.last_adgroup_id is not None
            return httpx.Response(
                200,
                headers={"content-type": "application/json;charset=utf-8"},
                json={
                    "result": {
                        "AdGroups": [
                            {
                                "Id": self.last_adgroup_id,
                                "CampaignId": 715840559,
                                "Name": "Ad Helper проверка API (ah-probe-1)",
                                "RegionIds": [213],
                            }
                        ]
                    }
                },
            )
        assert str(request.url) == entry["url"]
        if (
            request.url.path.endswith("/ads")
            and body.get("method") == "get"
            and entry["request"].get("method") == "add"
        ):
            # These older recordings predate the ads.get quota check that runs before ads.add.
            criteria = body.get("params", {}).get("SelectionCriteria", {})
            assert criteria.get("AdGroupIds")
            return httpx.Response(
                200,
                headers={"content-type": "application/json;charset=utf-8"},
                text='{"result":{"Ads":[]}}',
            )
        entry = self.entries.pop(0)
        if request.url.path.endswith("/adgroups") and body.get("method") == "add":
            response_data = json.loads(entry["body"])
            self.last_adgroup_id = response_data["result"]["AddResults"][0]["Id"]
        # Байты картинки в записи заменены их SHA-256: запись не тащит файл дважды.
        for image in body.get("params", {}).get("AdImages", []):
            data = base64.b64decode(image["ImageData"])
            image["ImageData"] = "sha256:" + hashlib.sha256(data).hexdigest()
        # Записи до 2026-11-05 вечера не просили AdImageHash: поле добавлено позже.
        names = body.get("params", {}).get("TextAdFieldNames")
        recorded = entry["request"].get("params", {}).get("TextAdFieldNames")
        if names and recorded is not None and "AdImageHash" not in recorded:
            names.remove("AdImageHash")
        # Запись 2026-11-05 создавала объявления запуска без метки в ссылке;
        # метку добавили 2026-11-08.
        recorded_ads = entry["request"].get("params", {}).get("Ads", [])
        for item, recorded_item in zip(
            body.get("params", {}).get("Ads", []), recorded_ads, strict=False
        ):
            href = item["TextAd"]["Href"]
            if "ah_ad=" in href and "ah_ad=" not in recorded_item["TextAd"]["Href"]:
                item["TextAd"]["Href"] = href.replace("?ah_ad=ah-probe-1", "")
        if "ReportName" in body.get("params", {}):
            body["params"].pop("ReportName")
            entry["request"]["params"].pop("ReportName")
        assert body == entry["request"]
        return httpx.Response(entry["status"], headers=entry["headers"], text=entry["body"])


async def test_recorded_reads_of_empty_account() -> None:
    replay = Replay(RECORDED / "read_empty_account.json")
    platform, client = _platform(replay)

    assert await platform.read_campaigns(ACCOUNT) == []
    spend = await platform.fetch_spend(ACCOUNT)
    assert spend.reported == 0
    assert spend.data_as_of < NOW
    assert await platform.fetch_balance(ACCOUNT) == 0
    assert await platform.find_ad(ACCOUNT, "ahc-1") is None
    assert await platform.read_ad(ACCOUNT, "ad:1") == {}
    assert await platform.read_targeting(ACCOUNT, "1") is None
    verdicts = await platform.moderation(ACCOUNT, ["1"])
    assert [v.state for v in verdicts] == [ModerationState.MODERATION]
    assert replay.entries == []
    assert client.units[ACCOUNT].limit == 160_000
    await client.aclose()


async def test_recorded_write_run_on_real_cabinet() -> None:
    """Боевой прогон 2026-11-05: ЕПК 300 ₽ на 6 дней, группа, объявления, пауза; без денег."""
    replay = Replay(RECORDED / "write_unified_campaign.json")
    client = DirectHttpClient(transport=replay, sleep=_no_sleep)
    platform = DirectPlatform(client, _token, lambda: datetime(2026, 11, 5, 7, 24, tzinfo=UTC))
    marker = "ah-probe-1"
    campaign = NewCampaign(
        marker=marker,
        name=f"Ad Helper проверка API ({marker})",
        placement=Placement.ALL,
        limit=300 * RUB,
        ends_at=datetime(2026, 11, 10, 21, 0, tzinfo=UTC),
        regions=(213,),
        href="https://example.com/",
        counters=(),
        ads=(NewAd("Проверка подключения", "Служебное объявление сервиса"),),
    )
    assert await platform.find_campaign(ACCOUNT, marker) is None
    await platform.create_campaign(ACCOUNT, campaign)
    found = await platform.find_campaign(ACCOUNT, marker)
    assert found is not None and found.campaign.planned() == campaign.planned()
    campaign_id = found.campaign_id
    [budget] = await platform.read_campaigns(ACCOUNT)
    assert (budget.limit, budget.active) == (300 * RUB, True)
    await platform.set_limit(ACCOUNT, LimitChange(campaign_id, 300 * RUB))
    assert (await platform.read_campaigns(ACCOUNT))[0].limit == 300 * RUB
    assert await platform.read_targeting(ACCOUNT, campaign_id) == Targeting(
        Schedule((0, 1, 2, 3, 4, 5, 6), 0, 24), (213,)
    )
    await platform.set_schedule(ACCOUNT, campaign_id, Schedule.of([0, 1, 2, 3, 4], 9, 21))
    await platform.set_regions(ACCOUNT, campaign_id, (213,))
    # Директ применяет расписание с задержкой: сразу после update читается прежнее,
    # журнал не закрывает запись и дочитает на следующем проходе.
    stale = await platform.read_targeting(ACCOUNT, campaign_id)
    assert stale is not None and stale.schedule == Schedule((0, 1, 2, 3, 4, 5, 6), 0, 24)
    href = "https://example.com/?utm_source=yandex"
    await platform.create_ad(
        ACCOUNT, campaign_id, "ahc-probe-1", "Проверка объявления", "Служебный текст сервиса", href
    )
    ad = await platform.find_ad(ACCOUNT, "ahc-probe-1")
    assert ad is not None and ad.href == href
    verdicts = await platform.moderation(ACCOUNT, [*found.ad_ids, ad.ad_id])
    assert {v.state for v in verdicts} == {ModerationState.MODERATION}
    await platform.set_ad_active(ACCOUNT, f"ad:{found.ad_ids[0]}", False)
    assert await platform.read_ad(ACCOUNT, f"ad:{found.ad_ids[0]}") == {
        "active": False,
        "archived": False,
    }
    await platform.pause_campaigns(ACCOUNT, [campaign_id])
    assert (await platform.read_campaigns(ACCOUNT))[0].active is False
    assert (await platform.fetch_spend(ACCOUNT)).reported == 0
    assert await platform.fetch_balance(ACCOUNT) == 0
    assert replay.entries == []
    await client.aclose()


# --- Транспорт ---


async def test_headers_carry_token_login_and_language(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    await platform.read_campaigns(ACCOUNT)
    headers = direct.requests[0]["headers"]
    assert headers["authorization"] == f"Bearer token-{ACCOUNT}"
    assert headers["client-login"] == ACCOUNT
    assert headers["accept-language"] == "ru"


@pytest.mark.parametrize(
    ("code", "error"),
    [
        (53, DirectAuthError),
        (513, DirectAuthError),
        (152, DirectPointsExhausted),
        (8000, DirectRejected),
    ],
)
async def test_request_errors_come_with_http_200(
    platform: DirectPlatform, direct: FakeDirect, code: int, error: type[Exception]
) -> None:
    direct.request_errors["campaigns.get"] = code
    with pytest.raises(error):
        await platform.read_campaigns(ACCOUNT)


async def test_points_rest_follows_units_header_and_exhaustion() -> None:
    direct = FakeDirect()
    platform, client = _platform(direct.transport())
    assert client.points_rest(ACCOUNT) is None
    await platform.read_campaigns(ACCOUNT)
    seen = client.points_rest(ACCOUNT)
    assert seen is not None and seen > 0
    direct.request_errors["campaigns.get"] = 152
    with pytest.raises(DirectPointsExhausted):
        await platform.read_campaigns(ACCOUNT)
    # Отказ по баллам без заголовка Units: остаток считаем нулём, оптимизацию откладываем.
    assert client.points_rest(ACCOUNT) == 0
    await client.aclose()


async def test_every_direct_error_is_platform_unavailable_for_the_ledger(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.request_errors["campaigns.get"] = 53
    with pytest.raises(PlatformUnavailable):
        await platform.read_campaigns(ACCOUNT)


async def test_reads_retry_server_failures(platform: DirectPlatform, direct: FakeDirect) -> None:
    direct.failures["campaigns.get"] = 2
    assert await platform.read_campaigns(ACCOUNT) == []
    assert direct.calls == ["campaigns.get"] * 3


async def test_reads_give_up_after_attempts(platform: DirectPlatform, direct: FakeDirect) -> None:
    direct.failures["campaigns.get"] = 3
    with pytest.raises(DirectError):
        await platform.read_campaigns(ACCOUNT)


async def test_changes_are_never_retried_blindly(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    """`add` мог примениться: повтор решает журнал после чтения, а не транспорт."""
    direct.failures["campaigns.add!"] = 1
    with pytest.raises(DirectError):
        await platform.create_campaign(ACCOUNT, launch())
    assert direct.calls.count("campaigns.add") == 1
    assert len(direct.campaigns) == 1


async def test_busy_server_retries_even_changes(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.request_errors["campaigns.add"] = 506
    await platform.create_campaign(ACCOUNT, launch())
    assert direct.calls.count("campaigns.add") == 2
    assert len(direct.campaigns) == 1


async def test_queued_report_waits_for_retry_in(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.report_queue = 2
    direct.report_rows = [("2026-11-04", "101", 300 * RUB)]
    snapshot = await platform.fetch_spend(ACCOUNT)
    assert snapshot.reported == 300 * RUB
    assert direct.calls.count("reports") == 3


async def test_business_spend_uses_only_its_managed_campaign_ids(
    direct: FakeDirect,
) -> None:
    client = DirectHttpClient(transport=direct.transport(), sleep=_no_sleep)
    platform = DirectPlatform(
        client,
        _token,
        lambda: NOW,
        lambda _account: _campaign_ids(),
    )
    direct.campaigns[101] = {
        "Id": 101,
        "Name": "Business A",
        "Type": "UNIFIED_CAMPAIGN",
        "State": "ON",
        "Status": "ACCEPTED",
        "UnifiedCampaign": {"BiddingStrategy": {}},
    }
    direct.campaigns[202] = {
        "Id": 202,
        "Name": "Business B",
        "Type": "UNIFIED_CAMPAIGN",
        "State": "ON",
        "Status": "ACCEPTED",
        "UnifiedCampaign": {"BiddingStrategy": {}},
    }
    direct.report_rows = [("2026-11-04", "101", 100), ("2026-11-04", "202", 900)]
    try:
        snapshot = await platform.fetch_spend(ACCOUNT)
        account_snapshot = await platform.fetch_account_spend(ACCOUNT)
        assert snapshot.reported == 100
        assert account_snapshot.reported == 1_000
        definition = direct.requests[-2]["body"]["params"]
        assert definition["ReportType"] == "CAMPAIGN_PERFORMANCE_REPORT"
        assert definition["SelectionCriteria"]["Filter"][0]["Values"] == ["101"]
    finally:
        await client.aclose()


async def test_managed_campaign_ids_resolves_campaigns_from_open_launch_markers(
    direct: FakeDirect,
) -> None:
    client = DirectHttpClient(transport=direct.transport(), sleep=_no_sleep)
    platform = DirectPlatform(
        client,
        _token,
        lambda: NOW,
        lambda _account: _campaign_ids(),
        lambda _account: _campaign_targets(),
    )
    direct.campaigns[101] = {
        "Id": 101,
        "Name": "Applied campaign",
        "Type": "UNIFIED_CAMPAIGN",
        "State": "ON",
    }
    direct.campaigns[303] = {
        "Id": 303,
        "Name": "Perm business (ah-1-1)",
        "Type": "UNIFIED_CAMPAIGN",
        "State": "ON",
    }
    try:
        assert await platform.managed_campaign_ids(ACCOUNT) == ("101", "303")
    finally:
        await client.aclose()


async def _campaign_ids() -> tuple[str, ...]:
    return ("101",)


async def _campaign_targets() -> tuple[str, ...]:
    return ("launch:ah-1-1",)


def test_rubles_become_micros_without_rounding() -> None:
    assert rubles_to_micros("1500.5") == 1_500_500_000
    assert rubles_to_micros("0") == 0
    with pytest.raises(ValueError):
        rubles_to_micros("0.0000001")


# --- Бюджет ---


@given(st.integers(min_value=1, max_value=10**13), st.integers(min_value=1, max_value=4))
def test_split_limit_never_exceeds_the_limit(total: int, parts: int) -> None:
    shares = split_limit(total, parts)
    assert sum(shares) == total
    assert all(share >= 0 for share in shares)


async def test_balance_comes_from_shared_account(platform: DirectPlatform) -> None:
    assert await platform.fetch_balance(ACCOUNT) == 1_500_500_000


async def test_balance_returns_zero_when_status_blocked(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.status_blocked = True
    assert await platform.fetch_balance(ACCOUNT) == 0


async def test_balance_returns_zero_when_negative_amount(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.amount = "-50.00"
    assert await platform.fetch_balance(ACCOUNT) == 0


async def test_balance_returns_zero_when_status_is_blocked(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.status = "Blocked"
    assert await platform.fetch_balance(ACCOUNT) == 0


async def test_balance_returns_zero_when_amount_none_or_empty(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.amount = None
    assert await platform.fetch_balance(ACCOUNT) == 0
    direct.amount = ""
    assert await platform.fetch_balance(ACCOUNT) == 0
    direct.amount = "   "
    assert await platform.fetch_balance(ACCOUNT) == 0


async def test_balance_raises_when_no_shared_account(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.no_shared_account = True
    with pytest.raises(DirectError, match="shared account is not enabled"):
        await platform.fetch_balance(ACCOUNT)


async def test_balance_case_insensitive_login(platform: DirectPlatform, direct: FakeDirect) -> None:
    assert await platform.fetch_balance(ACCOUNT.upper()) == 1_500_500_000


@pytest.mark.parametrize("placement", list(Placement))
async def test_launched_campaign_reads_back_its_limit(
    platform: DirectPlatform, direct: FakeDirect, placement: Placement
) -> None:
    await platform.create_campaign(ACCOUNT, launch(placement, limit=2_701 * RUB))
    [campaign] = await platform.read_campaigns(ACCOUNT)
    assert campaign.limit == 2_701 * RUB
    assert campaign.active is True


async def test_spent_counts_only_the_budget_period(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    await platform.create_campaign(ACCOUNT, launch())
    campaign_id = str(next(iter(direct.campaigns)))
    direct.report_rows = [
        ("2026-11-04", campaign_id, 999 * RUB),  # до начала периода
        ("2026-11-05", campaign_id, 120 * RUB),
    ]
    [campaign] = await platform.read_campaigns(ACCOUNT)
    assert campaign.spent == 120 * RUB


async def test_campaign_days_requests_campaign_report(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.report_rows = [("2026-11-09", "101", 57_550_000)]

    rows = await platform.campaign_days(ACCOUNT, ["101"], date(2026, 11, 9), date(2026, 11, 9))

    assert len(rows) == 1
    assert (rows[0].campaign_id, rows[0].clicks, rows[0].cost) == ("101", 0, 57_550_000)
    definition = direct.requests[-1]["body"]["params"]
    assert definition["ReportType"] == "CAMPAIGN_PERFORMANCE_REPORT"
    assert definition["FieldNames"] == [
        "Date",
        "CampaignId",
        "Impressions",
        "Clicks",
        "Cost",
        "Conversions",
    ]


async def test_campaign_without_period_budget_counts_as_unlimited(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.campaigns[5] = {
        "Id": 5,
        "Name": "Чужая",
        "Type": "TEXT_CAMPAIGN",
        "State": "ON",
        "TextCampaign": {
            "BiddingStrategy": {
                "Search": {
                    "BiddingStrategyType": "WB_MAXIMUM_CLICKS",
                    "WbMaximumClicks": {"WeeklySpendLimit": 300 * RUB},
                },
                "Network": {"BiddingStrategyType": "SERVING_OFF"},
            }
        },
    }
    [campaign] = await platform.read_campaigns(ACCOUNT)
    assert campaign.limit == NO_LIMIT
    with pytest.raises(DirectRejected):
        await platform.set_limit(ACCOUNT, LimitChange("5", 100 * RUB))


async def test_set_limit_through_journal_gateway_reaches_plan(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    await platform.create_campaign(ACCOUNT, launch(Placement.ALL))
    campaign_id = str(next(iter(direct.campaigns)))
    gateway = PlatformChangeGateway(platform)
    planned = {"limit": 1_999 * RUB + 1}
    await gateway.apply(ACCOUNT, ChangeKind.SET_LIMIT, campaign_id, planned)
    assert plan_reached(planned, await gateway.read(ACCOUNT, campaign_id))
    strategy = direct.campaigns[int(campaign_id)]["UnifiedCampaign"]["BiddingStrategy"]
    budget = strategy["Search"]["WbMaximumClicks"]["CustomPeriodBudget"]
    assert budget["SpendLimit"] == planned["limit"]
    # Дата периода не потерялась при обновлении, сети по-прежнему идут за поиском.
    assert budget["EndDate"] == "2026-12-04"
    assert strategy["Network"] == {"BiddingStrategyType": "NETWORK_DEFAULT"}


async def test_pause_and_resume_campaign(platform: DirectPlatform, direct: FakeDirect) -> None:
    await platform.create_campaign(ACCOUNT, launch())
    campaign_id = str(next(iter(direct.campaigns)))
    gateway = PlatformChangeGateway(platform)
    await gateway.apply(ACCOUNT, ChangeKind.PAUSE, campaign_id, {"active": False})
    assert (await gateway.read(ACCOUNT, campaign_id))["active"] is False
    await gateway.apply(ACCOUNT, ChangeKind.RESUME, campaign_id, {"active": True})
    assert (await gateway.read(ACCOUNT, campaign_id))["active"] is True


async def test_unreachable_direct_is_gateway_unavailable_for_the_journal(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    from adhelper.ledger.domain.ports import GatewayUnavailable

    direct.failures["campaigns.get"] = 3
    with pytest.raises(GatewayUnavailable):
        await PlatformChangeGateway(platform).read(ACCOUNT, "1")


# --- Запуск ---


@pytest.mark.parametrize("placement", list(Placement))
async def test_launch_reads_back_exactly_the_plan(
    platform: DirectPlatform, direct: FakeDirect, placement: Placement
) -> None:
    campaign = launch(placement)
    gateway = RoutingGateway(PlatformChangeGateway(platform), platform, platform, launcher=platform)
    assert await gateway.read(ACCOUNT, campaign.target_ref) == {}
    await gateway.apply(
        ACCOUNT, ChangeKind.CREATE_CAMPAIGN, campaign.target_ref, campaign.planned()
    )
    actual = await gateway.read(ACCOUNT, campaign.target_ref)
    assert plan_reached(campaign.planned(), actual)
    assert len(actual["ad_ids"]) == 2
    # Объявления ушли на проверку, кампания создана с периодом и без автопродления.
    assert {ad["Status"] for ad in direct.ads.values()} == {"MODERATION"}
    budget = next(iter(direct.campaigns.values()))["UnifiedCampaign"]["BiddingStrategy"]
    side, other = (
        ("Network", "Search") if placement is Placement.NETWORKS else ("Search", "Network")
    )
    assert budget[side]["WbMaximumClicks"]["CustomPeriodBudget"]["AutoContinue"] == "NO"
    follows = "NETWORK_DEFAULT" if placement is Placement.ALL else "SERVING_OFF"
    assert budget[other] == {"BiddingStrategyType": follows}


async def test_launch_retry_finishes_without_duplicates(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    """Кампания и группа создались, объявления нет: повтор доделывает только недостающее."""
    direct.failures["ads.add"] = 1
    with pytest.raises(DirectError):
        await platform.create_campaign(ACCOUNT, launch())
    await platform.create_campaign(ACCOUNT, launch())
    assert len(direct.campaigns) == 1
    assert len(direct.groups) == 1
    assert len(direct.ads) == 2
    found = await platform.find_campaign(ACCOUNT, "ah-k1-1")
    assert found is not None and plan_reached(launch().planned(), found.campaign.planned())


async def test_rejected_ad_text_is_a_platform_refusal(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    long = launch()
    long = NewCampaign(**{**long.__dict__, "ads": (NewAd("Я" * 57, "текст"),)})
    with pytest.raises(DirectRejected):
        await platform.create_campaign(ACCOUNT, long)


# --- Объявления ---


async def _launched(platform: DirectPlatform, direct: FakeDirect) -> str:
    await platform.create_campaign(ACCOUNT, launch())
    return str(next(iter(direct.campaigns)))


async def test_new_ad_is_found_by_marker_with_clean_href(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    campaign_id = await _launched(platform, direct)
    gateway = RoutingGateway(PlatformChangeGateway(platform), platform, publisher=platform)
    planned = {
        "campaign_id": campaign_id,
        "headline": "Маникюр за час",
        "text": "Запись онлайн",
        "href": "https://romashka.example/?utm_source=yandex#top",
    }
    assert await gateway.read(ACCOUNT, "newad:ahc-7") == {}
    await gateway.apply(ACCOUNT, ChangeKind.CREATE_AD, "newad:ahc-7", planned)
    actual = await gateway.read(ACCOUNT, "newad:ahc-7")
    assert plan_reached(planned, actual)
    stored = direct.ads[int(actual["ad_id"])]["TextAd"]["Href"]
    assert stored == "https://romashka.example/?utm_source=yandex&ah_ad=ahc-7#top"
    assert direct.ads[int(actual["ad_id"])]["Status"] == "MODERATION"


async def test_moderation_verdicts_in_our_words(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    await _launched(platform, direct)
    first, second = sorted(direct.ads)
    direct.ads[first]["Status"] = "ACCEPTED"
    direct.ads[second].update(Status="REJECTED", StatusClarification="Нарушение правил")
    verdicts = await platform.moderation(ACCOUNT, [str(first), str(second), "999"])
    assert [v.state for v in verdicts] == [
        ModerationState.ACCEPTED,
        ModerationState.REJECTED,
        ModerationState.MODERATION,
    ]
    assert verdicts[1].reasons == ("Нарушение правил",)


async def test_ad_suspend_and_resume(platform: DirectPlatform, direct: FakeDirect) -> None:
    await _launched(platform, direct)
    ref = f"ad:{min(direct.ads)}"
    await platform.set_ad_active(ACCOUNT, ref, False)
    assert await platform.read_ad(ACCOUNT, ref) == {"active": False, "archived": False}
    await platform.set_ad_active(ACCOUNT, ref, True)
    assert await platform.read_ad(ACCOUNT, ref) == {"active": True, "archived": False}


@pytest.mark.parametrize(
    "href",
    [
        "https://a.example/",
        "https://a.example/?x=1",
        "https://a.example/p#f",
        "https://a.example/?a=1&b=2#f",
    ],
)
def test_marker_round_trip(href: str) -> None:
    assert unmark_href(mark_href(href, "ahc-3")) == (href, "ahc-3")
    assert unmark_href(href) == (href, None)


# --- Где и когда показывать ---


async def test_schedule_and_regions_read_back_as_set(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    campaign_id = await _launched(platform, direct)
    assert await platform.read_targeting(ACCOUNT, campaign_id) == Targeting(
        Schedule((0, 1, 2, 3, 4, 5, 6), 0, 24), (213,)
    )
    schedule = Schedule.of([0, 1, 2, 3, 4], 9, 21)
    await platform.set_schedule(ACCOUNT, campaign_id, schedule)
    await platform.set_regions(ACCOUNT, campaign_id, (2, 213))
    assert await platform.read_targeting(ACCOUNT, campaign_id) == Targeting(schedule, (2, 213))


def test_schedule_items_format() -> None:
    items = schedule_items(Schedule.of([0], 9, 11))
    assert items[0] == "1," + ",".join(["0"] * 9 + ["100", "100"] + ["0"] * 13)
    assert items[6].startswith("7,0,")


def test_schedule_we_cannot_express_never_matches_a_plan() -> None:
    hours = ["0"] * 24
    hours[9] = "150"
    odd = parse_schedule({"Schedule": {"Items": ["1," + ",".join(hours)]}})
    assert odd.days == ()


async def test_recorded_ad_with_image_on_real_cabinet() -> None:
    """Боевой прогон 2026-11-05: AdImages.add дважды и объявление с картинкой в кампании
    715840559 на паузе; объявление сразу приостановлено, денег не тратили."""
    replay = Replay(RECORDED / "write_ad_image.json")
    platform, client = _platform(replay)
    data = (RECORDED / "probe_image.jpg").read_bytes()
    image = AdImage(hashlib.sha256(data).hexdigest(), data, "ahc-image-probe")
    first = await platform.upload_image(ACCOUNT, image)
    # Одинаковую картинку Директ хранит один раз: повтор загрузки безопасен.
    assert await platform.upload_image(ACCOUNT, image) == first == "LQXku-UheMCVqo5QilyIOg"
    marker = "ahc-probe-img-1"
    assert await platform.find_ad(ACCOUNT, marker) is None
    # Ответ с предупреждением 10251: текстовое объявление стало комбинаторным, картинка осталась.
    await platform.create_ad(
        ACCOUNT,
        "715840559",
        marker,
        "Проверка картинки",
        "Служебный текст сервиса",
        "https://example.com/",
        image_hash=first,
    )
    ad = await platform.find_ad(ACCOUNT, marker)
    assert ad is not None and ad.image_hash == first and ad.group_id == "5820622013"
    await platform.set_ad_active(ACCOUNT, f"ad:{ad.ad_id}", False)
    assert await platform.read_ad(ACCOUNT, f"ad:{ad.ad_id}") == {"active": False, "archived": False}
    assert replay.entries == []
    await client.aclose()


async def test_archived_launch_campaign_is_not_reused(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    """Архивную кампанию запуска нельзя доделывать: объявления в ней не покажутся."""
    await platform.create_campaign(ACCOUNT, launch())
    archived = next(iter(direct.campaigns))
    direct.campaigns[archived]["State"] = "ARCHIVED"
    await platform.create_campaign(ACCOUNT, launch())
    live = [c for c in direct.campaigns.values() if c["State"] != "ARCHIVED"]
    assert len(live) == 1
    assert live[0]["Id"] != archived
    found = await platform.find_campaign(ACCOUNT, "ah-k1-1")
    assert found is not None and found.campaign_id != str(archived)


async def test_launch_ads_carry_the_marker_in_href(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    """Объявления запуска помечаются так же, как созданные вручную: метка в ссылке."""
    await platform.create_campaign(ACCOUNT, launch())
    stored = {ad["TextAd"]["Href"] for ad in direct.ads.values()}
    assert stored == {"https://romashka.example/?ah_ad=ah-k1-1"}
    found = await platform.find_campaign(ACCOUNT, "ah-k1-1")
    assert found is not None
    assert plan_reached(launch().planned(), found.campaign.planned())


async def test_archived_campaign_does_not_count_toward_account_limit(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    """Архивная кампания не тратит деньги: её лимит не занимает место в бюджете кабинета."""
    await platform.create_campaign(ACCOUNT, launch(limit=1_150 * RUB))
    archived = next(iter(direct.campaigns))
    direct.campaigns[archived]["State"] = "ARCHIVED"
    await platform.create_campaign(ACCOUNT, launch(limit=2_700 * RUB))
    counted = await platform.read_account_campaigns(ACCOUNT)
    assert str(archived) not in [c.campaign_id for c in counted]
    assert sum(c.limit for c in counted) == 2_700 * RUB


@pytest.mark.parametrize(
    ("search", "network", "auto", "learns"),
    [
        ("WB_MAXIMUM_CLICKS", "NETWORK_DEFAULT", True, False),
        ("WB_MAXIMUM_CLICKS", "SERVING_OFF", True, False),
        ("HIGHEST_POSITION", "SERVING_OFF", False, False),
        ("AVERAGE_CPA", "NETWORK_DEFAULT", True, None),
        ("SOMETHING_NEW", "SERVING_OFF", None, None),
        ("SERVING_OFF", "SERVING_OFF", None, None),
    ],
)
def test_strategy_of_tells_auto_strategy_and_learning(
    search: str, network: str, auto: bool | None, learns: bool | None
) -> None:
    strategy = {
        "Search": {"BiddingStrategyType": search},
        "Network": {"BiddingStrategyType": network},
    }
    result = strategy_of(strategy, "2026-10-01")
    assert (result.auto, result.learns) == (auto, learns)
    assert result.started == date(2026, 10, 1)
    assert strategy_of(strategy, "не дата").started is None


async def test_reports_count_only_lead_goals_when_the_counter_is_known(
    direct: FakeDirect,
) -> None:
    async def goals(account: str) -> list[int]:
        return [11, 12]

    client = DirectHttpClient(transport=direct.transport(), sleep=_no_sleep)
    platform = DirectPlatform(client, _token, lambda: NOW, goals_for=goals)
    direct.report_rows = [("2026-11-09", "101", 57_550_000)]

    ad_rows = await platform.ad_days(ACCOUNT, ["101"], date(2026, 11, 9), date(2026, 11, 9))
    campaign_rows = await platform.campaign_days(
        ACCOUNT, ["101"], date(2026, 11, 9), date(2026, 11, 9)
    )

    assert direct.requests[-1]["body"]["params"]["Goals"] == [11, 12]
    assert direct.requests[-2]["body"]["params"]["Goals"] == [11, 12]
    # По два обращения на каждую из двух целей.
    assert ad_rows[0].conversions == 4
    assert campaign_rows[0].conversions == 4
    await client.aclose()


async def test_reports_without_goals_do_not_send_a_goal_filter(
    platform: DirectPlatform, direct: FakeDirect
) -> None:
    direct.report_rows = [("2026-11-09", "101", 57_550_000)]
    await platform.ad_days(ACCOUNT, ["101"], date(2026, 11, 9), date(2026, 11, 9))
    assert "Goals" not in direct.requests[-1]["body"]["params"]
