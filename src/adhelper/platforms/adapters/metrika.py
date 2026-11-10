"""Клиент Метрики: цели-обращения и визиты с рекламы. docs/03-integrations.md §3.

Токен тот же, что у Директа, кабинет один. Сбой, отказ и лимит запросов — `PlatformUnavailable`:
визиты и цели тогда «неизвестны», а не нули, и проверки качества данных их пропускают.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date
from typing import Any

import httpx

from adhelper.budget.domain.ports import PlatformUnavailable

# Цели, которые считаем обращением: звонок, заявка, сообщение. Просмотры страниц, глубина,
# прокрутка и прочие служебные цели обращениями не считаются.
LEAD_GOAL_TYPES = frozenset({"phone", "form", "messenger", "email", "call"})
TIMEOUT = 20.0


def lead_goal_ids(goals: Sequence[Mapping[str, Any]]) -> list[int]:
    return sorted(int(g["id"]) for g in goals if g.get("type") in LEAD_GOAL_TYPES)


class MetrikaClient:
    """Метрика одного кабинета: токен берётся при каждом вызове, в логи не попадает."""

    def __init__(
        self,
        token: Callable[[], Awaitable[str]],
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._transport = transport

    async def _get(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        try:
            token = await self._token()
            async with httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT) as client:
                response = await client.get(
                    self._base + path,
                    params=dict(params or {}),
                    headers={"Authorization": f"OAuth {token}"},
                )
            if response.status_code != httpx.codes.OK:
                raise PlatformUnavailable(f"metrika status {response.status_code}")
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise PlatformUnavailable("metrika unreachable") from error

    async def lead_goals(self, counter: str) -> list[int]:
        body = await self._get(f"/management/v1/counter/{counter}/goals")
        return lead_goal_ids(body.get("goals") or [])

    async def ad_visits(self, counter: str, since: date, until: date) -> int | None:
        """Визиты с рекламных источников за московские дни; None — Метрика данных не дала."""
        body = await self._get(
            "/stat/v1/data",
            {
                "ids": counter,
                "metrics": "ym:s:visits",
                "date1": since.isoformat(),
                "date2": until.isoformat(),
                "filters": "ym:s:lastTrafficSource=='ad'",
                "accuracy": "full",
                "timezone": "+03:00",
            },
        )
        totals = body.get("totals") or []
        if not totals or not totals[0]:
            return None
        return int(totals[0][0])
