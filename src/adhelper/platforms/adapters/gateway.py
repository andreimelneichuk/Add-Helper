from collections.abc import Mapping
from typing import Any

from adhelper.budget.domain.ports import PlatformUnavailable
from adhelper.campaigns.domain.ports import TARGETING_PREFIX, TargetingControl
from adhelper.campaigns.domain.targeting import Schedule
from adhelper.ledger.domain.model import ChangeKind
from adhelper.ledger.domain.ports import GatewayUnavailable, PlatformGateway
from adhelper.platforms.domain.ports import AdControl

AD_PREFIX = "ad:"


class RoutingGateway:
    """Исполнитель журнала: кампании идут в бюджетный шлюз, объявления и показ сюда."""

    def __init__(
        self,
        campaigns: PlatformGateway,
        ads: AdControl,
        targeting: TargetingControl | None = None,
    ) -> None:
        self._campaigns = campaigns
        self._ads = ads
        self._targeting = targeting

    def _targeting_control(self) -> TargetingControl:
        if self._targeting is None:
            raise GatewayUnavailable("targeting is not connected")
        return self._targeting

    async def read(self, account: str, target_ref: str) -> Mapping[str, Any]:
        try:
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
