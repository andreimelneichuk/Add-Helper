from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from adhelper.business.domain.site import SiteRead


class FetchProblem(StrEnum):
    BAD_URL = "bad_url"
    PRIVATE = "private"
    UNREACHABLE = "unreachable"
    HTTP_ERROR = "http_error"
    NOT_HTML = "not_html"
    TOO_MANY_REDIRECTS = "too_many_redirects"


class SiteUnavailable(Exception):
    def __init__(self, problem: FetchProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


@dataclass(frozen=True)
class Link:
    url: str
    text: str


@dataclass(frozen=True)
class FetchedPage:
    """Страница после очистки: видимый текст и ссылки. Разметки и скриптов здесь нет."""

    url: str
    text: str
    links: Sequence[Link] = ()
    # Номера счётчиков Метрики из разметки: в текст для модели скрипты не попадают.
    counters: tuple[int, ...] = ()


class SiteFetcher(Protocol):
    async def fetch(self, url: str) -> FetchedPage:
        """Скачивает одну страницу с лимитами. Ошибка — SiteUnavailable."""
        ...


@dataclass(frozen=True)
class ClaimedRead:
    id: int
    url: str
    rules_version: str


class SiteReadQueue(Protocol):
    """Разборы всех бизнесов для воркера. Повтор безопасен: готовый разбор не берётся."""

    async def claim(
        self, now: datetime, stale_before: datetime, max_attempts: int
    ) -> ClaimedRead | None: ...

    async def finish(self, read_id: int, result: "SiteRead", model: str, now: datetime) -> None: ...
