from collections.abc import Mapping
from typing import Any

from adhelper.budget.domain.ports import PlatformUnavailable
from adhelper.campaigns.domain.launch import LAUNCH_PREFIX, NewCampaign
from adhelper.campaigns.domain.ports import (
    TARGETING_PREFIX,
    CampaignLauncher,
    TargetingControl,
)
from adhelper.campaigns.domain.targeting import Schedule
from adhelper.ledger.domain.model import ChangeKind
from adhelper.ledger.domain.ports import GatewayUnavailable, PlatformGateway
from adhelper.platforms.domain.ports import AdControl, AdPublisher

AD_PREFIX = "ad:"
# Цель записи о новом объявлении: его ещё нет, находим по нашей метке.
NEW_AD_PREFIX = "newad:"


class RoutingGateway:
    """Исполнитель журнала: кампании идут в бюджетный шлюз, объявления и показ сюда."""

    def __init__(
        self,
        campaigns: PlatformGateway,
        ads: AdControl,
        targeting: TargetingControl | None = None,
        launcher: CampaignLauncher | None = None,
        publisher: AdPublisher | None = None,
    ) -> None:
        self._campaigns = campaigns
        self._ads = ads
        self._targeting = targeting
        self._launcher = launcher
        self._publisher = publisher

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
        return {
            "campaign_id": found.campaign_id,
            "headline": found.headline,
            "text": found.text,
            "href": found.href,
            "ad_id": found.ad_id,
        }

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
                await self._publisher_control().create_ad(
                    account,
                    str(planned["campaign_id"]),
                    target_ref.removeprefix(NEW_AD_PREFIX),
                    str(planned["headline"]),
                    str(planned["text"]),
                    str(planned["href"]),
                )
                return
            if target_ref.startswith(AD_PREFIX):
                await self._ads.set_ad_active(account, target_ref, planned["active"])
                return
            if target_ref.startswith(TARGETING_PREFIX):
                await self._apply_targeting(account, kind, target_ref, planned)
                return
        except PlatformUnavailable as error:
            raise GatewayUnavailable(str(error)) from error
        await self._campaigns.apply(account, kind, target_ref, planned)

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
