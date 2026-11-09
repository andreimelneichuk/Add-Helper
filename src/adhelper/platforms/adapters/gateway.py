from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from adhelper.budget.domain.ports import PlatformRejected, PlatformUnavailable
from adhelper.campaigns.domain.launch import LAUNCH_PREFIX, NewCampaign
from adhelper.campaigns.domain.ports import (
    TARGETING_PREFIX,
    CampaignLauncher,
    TargetingControl,
)
from adhelper.campaigns.domain.targeting import Schedule
from adhelper.ledger.domain.model import ChangeKind
from adhelper.ledger.domain.ports import ChangeRejected, GatewayUnavailable, PlatformGateway
from adhelper.platforms.domain.ports import (
    CLIENT_PREFIX,
    AdControl,
    AdPublisher,
    Advertiser,
    AdvertiserRegistry,
    ImageUploader,
    ImageVault,
)

AD_PREFIX = "ad:"
# Цель записи о новом объявлении: его ещё нет, находим по нашей метке.
NEW_AD_PREFIX = "newad:"
# Откуда исполнитель берёт реквизиты рекламодателя: в журнале их нет, только `card`.
AdvertiserSource = Callable[[], Awaitable[Advertiser | None]]


class RoutingGateway:
    """Исполнитель журнала: кампании идут в бюджетный шлюз, объявления и показ сюда."""

    def __init__(
        self,
        campaigns: PlatformGateway,
        ads: AdControl,
        targeting: TargetingControl | None = None,
        launcher: CampaignLauncher | None = None,
        publisher: AdPublisher | None = None,
        registry: AdvertiserRegistry | None = None,
        advertiser: AdvertiserSource | None = None,
        images: ImageVault | None = None,
        uploader: ImageUploader | None = None,
    ) -> None:
        self._campaigns = campaigns
        self._ads = ads
        self._targeting = targeting
        self._launcher = launcher
        self._publisher = publisher
        self._registry = registry
        self._advertiser = advertiser
        self._images = images
        self._uploader = uploader

    def _registry_control(self) -> AdvertiserRegistry:
        if self._registry is None:
            raise GatewayUnavailable("advertiser registry is not connected")
        return self._registry

    async def _read_client(self, account: str) -> Mapping[str, Any]:
        if self._advertiser is None:
            # Процесс без ключа реквизитов не берётся за запись: попытки не тратятся,
            # её доведёт запуск, у которого ключ есть.
            raise GatewayUnavailable("advertiser source is not connected")
        found = await self._registry_control().read_advertiser(account)
        return {} if found is None else found.card()

    async def _apply_client(
        self, account: str, kind: ChangeKind, planned: Mapping[str, Any]
    ) -> None:
        if kind is not ChangeKind.SET_ADVERTISER:
            raise ValueError(f"{kind} не меняет рекламодателя")
        registry = self._registry_control()
        if self._advertiser is None:
            raise GatewayUnavailable("advertiser source is not connected")
        advertiser = await self._advertiser()
        if advertiser is None or advertiser.card() != dict(planned):
            # Владелец изменил данные после записи: эта запись исчерпает попытки и закроется,
            # новые данные уйдут новой записью при следующем запуске.
            raise GatewayUnavailable("advertiser changed after the change was recorded")
        await registry.set_advertiser(account, advertiser)

    def _publisher_control(self) -> AdPublisher:
        if self._publisher is None:
            raise GatewayUnavailable("publisher is not connected")
        return self._publisher

    async def _read_new_ad(self, account: str, target_ref: str) -> Mapping[str, Any]:
        found = await self._publisher_control().find_ad(
            account, target_ref.removeprefix(NEW_AD_PREFIX)
        )
        if found is None:
            return {}
        # Сверх плана номер объявления: по нему читаем проверку и ставим в витрину.
        actual: dict[str, Any] = {
            "campaign_id": found.campaign_id,
            "headline": found.headline,
            "text": found.text,
            "href": found.href,
            "ad_id": found.ad_id,
            "group_id": found.group_id,
        }
        if found.image_hash and self._images is not None:
            # В плане наш sha256, на площадке её номер: сверяем по запомненной паре.
            actual["image"] = await self._images.sha_of(account, found.image_hash)
        return actual

    async def _image_hash(self, account: str, sha256: str) -> str:
        """Номер картинки на площадке: из памяти или загрузкой. Повтор загрузки безопасен."""
        if self._images is None or self._uploader is None:
            raise GatewayUnavailable("images are not connected")
        known = await self._images.platform_hash(account, sha256)
        if known is not None:
            return known
        image = await self._images.image_by_sha(sha256)
        if image is None:
            raise GatewayUnavailable("image is missing")
        uploaded = await self._uploader.upload_image(account, image)
        await self._images.remember_hash(account, sha256, uploaded)
        return uploaded

    def _launcher_control(self) -> CampaignLauncher:
        if self._launcher is None:
            raise GatewayUnavailable("launcher is not connected")
        return self._launcher

    async def _read_launch(self, account: str, target_ref: str) -> Mapping[str, Any]:
        found = await self._launcher_control().find_campaign(
            account, target_ref.removeprefix(LAUNCH_PREFIX)
        )
        if found is None:
            return {}
        # Сверх плана: номера, по которым витрина и журнал найдут созданное.
        return {
            **found.campaign.planned(),
            "campaign_id": found.campaign_id,
            "ad_ids": list(found.ad_ids),
        }

    def _targeting_control(self) -> TargetingControl:
        if self._targeting is None:
            raise GatewayUnavailable("targeting is not connected")
        return self._targeting

    async def read(self, account: str, target_ref: str) -> Mapping[str, Any]:
        try:
            if target_ref.startswith(LAUNCH_PREFIX):
                return await self._read_launch(account, target_ref)
            if target_ref.startswith(NEW_AD_PREFIX):
                return await self._read_new_ad(account, target_ref)
            if target_ref.startswith(CLIENT_PREFIX):
                return await self._read_client(account)
            if target_ref.startswith(AD_PREFIX):
                return await self._ads.read_ad(account, target_ref)
            if target_ref.startswith(TARGETING_PREFIX):
                campaign_id = target_ref.removeprefix(TARGETING_PREFIX)
                found = await self._targeting_control().read_targeting(account, campaign_id)
                if found is None:
                    return {}
                return {
                    "schedule": found.schedule.as_planned(),
                    "regions": list(found.regions),
                }
        except PlatformUnavailable as error:
            raise GatewayUnavailable(str(error)) from error
        return await self._campaigns.read(account, target_ref)

    async def apply(
        self, account: str, kind: ChangeKind, target_ref: str, planned: Mapping[str, Any]
    ) -> None:
        try:
            if target_ref.startswith(LAUNCH_PREFIX):
                if kind is not ChangeKind.CREATE_CAMPAIGN:
                    raise ValueError(f"{kind} не создаёт кампанию")
                marker = target_ref.removeprefix(LAUNCH_PREFIX)
                campaign = NewCampaign.from_planned(marker, planned)
                await self._launcher_control().create_campaign(account, campaign)
                return
            if target_ref.startswith(NEW_AD_PREFIX):
                if kind is not ChangeKind.CREATE_AD:
                    raise ValueError(f"{kind} не создаёт объявление")
                publisher = self._publisher_control()
                image = planned.get("image")
                image_hash = await self._image_hash(account, str(image)) if image else None
                await publisher.create_ad(
                    account,
                    str(planned["campaign_id"]),
                    target_ref.removeprefix(NEW_AD_PREFIX),
                    str(planned["headline"]),
                    str(planned["text"]),
                    str(planned["href"]),
                    group_id=str(planned["group_id"]) if planned.get("group_id") else None,
                    image_hash=image_hash,
                    group_name=str(planned["group_name"]) if planned.get("group_name") else None,
                    keywords=tuple(str(k) for k in planned.get("keywords", ())),
                )
                return
            if target_ref.startswith(CLIENT_PREFIX):
                await self._apply_client(account, kind, planned)
                return
            if target_ref.startswith(AD_PREFIX):
                await self._ads.set_ad_active(account, target_ref, planned["active"])
                return
            if target_ref.startswith(TARGETING_PREFIX):
                await self._apply_targeting(account, kind, target_ref, planned)
                return
            await self._campaigns.apply(account, kind, target_ref, planned)
        except PlatformRejected as error:
            # Ответ пришёл: отказ по объекту, а не «не ответил».
            raise ChangeRejected(str(error)) from error
        except PlatformUnavailable as error:
            raise GatewayUnavailable(str(error)) from error

    async def _apply_targeting(
        self, account: str, kind: ChangeKind, target_ref: str, planned: Mapping[str, Any]
    ) -> None:
        control = self._targeting_control()
        campaign_id = target_ref.removeprefix(TARGETING_PREFIX)
        if kind is ChangeKind.SET_SCHEDULE:
            schedule = Schedule.from_planned(planned["schedule"])
            await control.set_schedule(account, campaign_id, schedule)
        elif kind is ChangeKind.SET_REGIONS:
            await control.set_regions(account, campaign_id, tuple(planned["regions"]))
        else:
            raise ValueError(f"{kind} не меняет показ")
