from collections.abc import Mapping
from typing import Any, Protocol


class AdControl(Protocol):
    """Включение и приостановка объявлений. Удаления нет: откат должен оставаться возможным."""

    async def read_ad(self, account: str, ad_ref: str) -> Mapping[str, Any]: ...
    async def set_ad_active(self, account: str, ad_ref: str, active: bool) -> None: ...
