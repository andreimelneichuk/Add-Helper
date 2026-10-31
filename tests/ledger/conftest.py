from collections.abc import Mapping
from typing import Any

from adhelper.ledger.domain.model import ChangeKind
from adhelper.ledger.domain.ports import GatewayUnavailable


class FakeGateway:
    def __init__(self, state: dict[str, dict[str, Any]]) -> None:
        self.state = state
        self.apply_calls: list[tuple[str, Mapping[str, Any]]] = []
        self.unavailable = False
        self.lose_next_response = False

    async def read(self, account: str, target_ref: str) -> Mapping[str, Any]:
        if self.unavailable:
            raise GatewayUnavailable("read")
        return dict(self.state[target_ref])

    async def apply(
        self, account: str, kind: ChangeKind, target_ref: str, planned: Mapping[str, Any]
    ) -> None:
        if self.unavailable:
            raise GatewayUnavailable("apply")
        self.state[target_ref].update(planned)
        self.apply_calls.append((target_ref, dict(planned)))
        if self.lose_next_response:
            self.lose_next_response = False
            raise GatewayUnavailable("ответ потерян")
