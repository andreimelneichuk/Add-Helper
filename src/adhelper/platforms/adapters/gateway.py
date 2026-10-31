from collections.abc import Mapping
from typing import Any

from adhelper.budget.domain.ports import PlatformUnavailable
from adhelper.ledger.domain.model import ChangeKind
from adhelper.ledger.domain.ports import GatewayUnavailable, PlatformGateway
from adhelper.platforms.domain.ports import AdControl

AD_PREFIX = "ad:"


class RoutingGateway:
    """Исполнитель журнала: кампании идут в бюджетный шлюз, объявления сюда."""

    def __init__(self, campaigns: PlatformGateway, ads: AdControl) -> None:
        self._campaigns = campaigns
        self._ads = ads

    async def read(self, account: str, target_ref: str) -> Mapping[str, Any]:
        if not target_ref.startswith(AD_PREFIX):
            return await self._campaigns.read(account, target_ref)
        try:
            return await self._ads.read_ad(account, target_ref)
        except PlatformUnavailable as error:
            raise GatewayUnavailable(str(error)) from error

    async def apply(
        self, account: str, kind: ChangeKind, target_ref: str, planned: Mapping[str, Any]
    ) -> None:
        if not target_ref.startswith(AD_PREFIX):
            await self._campaigns.apply(account, kind, target_ref, planned)
            return
        try:
            await self._ads.set_ad_active(account, target_ref, planned["active"])
        except PlatformUnavailable as error:
            raise GatewayUnavailable(str(error)) from error
