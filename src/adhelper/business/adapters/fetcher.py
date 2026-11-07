"""Скачивание сайта владельца без доступа во внутреннюю сеть (docs/02, SSRF).

Адрес проверяется дважды: строкой до запроса и каждым IP при соединении. Проверка IP
стоит в сетевом слое, поэтому её не обходят ни перенаправление, ни смена DNS между
проверкой и соединением: соединяемся с тем адресом, который проверили.
"""

import asyncio
import re
import socket
import ssl
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import anyio
import httpcore
import httpx

from adhelper.business.adapters.html_text import page_from_html, page_from_text
from adhelper.business.domain.ports import FetchedPage, FetchProblem, SiteUnavailable
from adhelper.business.domain.site import SiteRules, UrlProblem, check_url, is_public_address

USER_AGENT = "AdHelper/1.0 (site reader; owner request)"
HTML_TYPES = ("text/html", "application/xhtml+xml")
REDIRECTS = frozenset({301, 302, 303, 307, 308})
CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([a-zA-Z0-9_-]+)""", re.IGNORECASE)


class BlockedAddress(Exception):
    """Имя ведёт во внутреннюю сеть."""


class GuardedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, allow: Callable[[str], bool] = is_public_address) -> None:
        self._inner = httpcore.AnyIOBackend()
        self._allow = allow

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 — подпись задаёт httpcore
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise httpcore.ConnectError(type(error).__name__) from None
        addresses = list(dict.fromkeys(str(info[4][0]).split("%")[0] for info in infos))
        # Хоть один внутренний адрес у имени — отказ: какой выберет соединение, не угадать.
        if not addresses or not all(self._allow(address) for address in addresses):
            raise BlockedAddress(host)
        last: Exception = httpcore.ConnectError(host)
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address, port, timeout, local_address, socket_options
                )
            except httpcore.ConnectError as error:
                last = error
        raise last

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 — подпись задаёт httpcore
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise BlockedAddress(path)

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _GuardedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, backend: httpcore.AsyncNetworkBackend) -> None:
        super().__init__()
        # Пул httpx с нашим сетевым слоем: остальное поведение транспорта прежнее.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=4,
            network_backend=backend,
        )


def _decode(body: bytes, header_charset: str | None) -> str:
    candidates = [header_charset] if header_charset else []
    found = CHARSET.search(body[:4096])
    if found:
        candidates.append(found.group(1).decode("ascii"))
    candidates += ["utf-8", "cp1251"]
    for charset in candidates:
        try:
            return body.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


UrlCheck = Callable[[str, SiteRules], tuple[str, UrlProblem | None]]


def checked_url(url: str, rules: SiteRules, url_check: UrlCheck = check_url) -> str:
    normalized, problem = url_check(url, rules)
    if problem is UrlProblem.PRIVATE:
        raise SiteUnavailable(FetchProblem.PRIVATE)
    if problem is not None:
        raise SiteUnavailable(FetchProblem.BAD_URL)
    return normalized


@dataclass(frozen=True)
class Resource:
    status: int
    headers: list[tuple[str, str]]
    body: bytes


class TooBig(Exception):
    """Ответ больше лимита: обрезанный скрипт браузеру не нужен."""


# Заголовки соединения задаёт наш клиент, а не браузер. Тело браузер получает распакованным:
# лимит считается по распакованным байтам, поэтому сжатая «бомба» его не обходит.
HOP_HEADERS = frozenset(
    {
        "host",
        "connection",
        "content-length",
        "accept-encoding",
        "content-encoding",
        "transfer-encoding",
        "keep-alive",
    }
)


class GuardedClient:
    """Один запрос без перехода по перенаправлению: им пользуется браузер (docs/02, SSRF).

    Перенаправление браузер выполняет новым запросом, и тот проходит ту же проверку.
    """

    def __init__(
        self,
        rules: SiteRules,
        allow_address: Callable[[str], bool] = is_public_address,
        url_check: UrlCheck = check_url,
    ) -> None:
        self._rules = rules
        self._url_check = url_check
        self._client = httpx.AsyncClient(
            transport=_GuardedTransport(GuardedBackend(allow_address)),
            timeout=rules.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, max_bytes: int
    ) -> Resource:
        target = checked_url(url, self._rules, self._url_check)
        sent = {k: v for k, v in headers.items() if k.lower() not in HOP_HEADERS}
        sent["Accept-Encoding"] = "identity"
        try:
            async with self._client.stream(
                method, target, headers=sent, content=body or None
            ) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data += chunk
                    if len(data) > max_bytes:
                        raise TooBig(url)
                kept = [
                    (k, v)
                    for k, v in response.headers.multi_items()
                    if k.lower() not in HOP_HEADERS
                ]
                return Resource(response.status_code, kept, bytes(data))
        except BlockedAddress:
            raise SiteUnavailable(FetchProblem.PRIVATE) from None
        except httpx.HTTPError:
            raise SiteUnavailable(FetchProblem.UNREACHABLE) from None


class SafeFetcher:
    def __init__(
        self,
        rules: SiteRules,
        allow_address: Callable[[str], bool] = is_public_address,
        url_check: UrlCheck = check_url,
    ) -> None:
        self._rules = rules
        self._backend = GuardedBackend(allow_address)
        self._url_check = url_check
        self._markers = rules.render.markers if rules.render else ()

    def _checked(self, url: str) -> str:
        return checked_url(url, self._rules, self._url_check)

    async def fetch(self, url: str) -> FetchedPage:
        try:
            async with asyncio.timeout(self._rules.timeout_seconds * 2):
                return await self._fetch(url)
        except TimeoutError:
            raise SiteUnavailable(FetchProblem.UNREACHABLE) from None

    async def _fetch(self, url: str) -> FetchedPage:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,text/plain;q=0.9",
            # Сжатие не просим: распакованный ответ мог бы оказаться больше лимита.
            "Accept-Encoding": "identity",
        }
        async with httpx.AsyncClient(
            transport=_GuardedTransport(self._backend),
            timeout=self._rules.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        ) as client:
            current = self._checked(url)
            for _ in range(self._rules.max_redirects + 1):
                try:
                    async with client.stream("GET", current) as response:
                        if response.status_code in REDIRECTS:
                            location = response.headers.get("location", "")
                            current = self._checked(str(response.url.join(location)))
                            continue
                        return await self._read(current, response)
                except BlockedAddress:
                    raise SiteUnavailable(FetchProblem.PRIVATE) from None
                except httpx.HTTPError:
                    raise SiteUnavailable(FetchProblem.UNREACHABLE) from None
            raise SiteUnavailable(FetchProblem.TOO_MANY_REDIRECTS)

    async def get_bytes(
        self, url: str, accept: str, mimes: tuple[str, ...], max_bytes: int
    ) -> tuple[str, str, bytes]:
        """Файл с сайта тем же защищённым путём: адрес, тип и тело не больше max_bytes."""
        try:
            async with asyncio.timeout(self._rules.timeout_seconds * 2):
                return await self._get_bytes(url, accept, mimes, max_bytes)
        except TimeoutError:
            raise SiteUnavailable(FetchProblem.UNREACHABLE) from None

    async def _get_bytes(
        self, url: str, accept: str, mimes: tuple[str, ...], max_bytes: int
    ) -> tuple[str, str, bytes]:
        headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "identity"}
        async with httpx.AsyncClient(
            transport=_GuardedTransport(self._backend),
            timeout=self._rules.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        ) as client:
            current = self._checked(url)
            for _ in range(self._rules.max_redirects + 1):
                try:
                    async with client.stream("GET", current) as response:
                        if response.status_code in REDIRECTS:
                            location = response.headers.get("location", "")
                            current = self._checked(str(response.url.join(location)))
                            continue
                        if response.status_code != 200:
                            raise SiteUnavailable(FetchProblem.HTTP_ERROR)
                        mime = (
                            response.headers.get("content-type", "").split(";")[0].strip().lower()
                        )
                        if mime not in mimes:
                            raise SiteUnavailable(FetchProblem.NOT_HTML)
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body += chunk
                            if len(body) > max_bytes:
                                raise SiteUnavailable(FetchProblem.NOT_HTML)
                        return current, mime, bytes(body)
                except BlockedAddress:
                    raise SiteUnavailable(FetchProblem.PRIVATE) from None
                except httpx.HTTPError:
                    raise SiteUnavailable(FetchProblem.UNREACHABLE) from None
            raise SiteUnavailable(FetchProblem.TOO_MANY_REDIRECTS)

    async def _read(self, url: str, response: httpx.Response) -> FetchedPage:
        if response.status_code != 200:
            raise SiteUnavailable(FetchProblem.HTTP_ERROR)
        content_type = response.headers.get("content-type", "").lower()
        mime = content_type.split(";")[0].strip()
        if mime not in (*HTML_TYPES, "text/plain"):
            raise SiteUnavailable(FetchProblem.NOT_HTML)
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            # Большую страницу не отвергаем: начала хватает для профиля.
            if len(body) >= self._rules.max_page_bytes:
                del body[self._rules.max_page_bytes :]
                break
        text = _decode(bytes(body), response.charset_encoding)
        if mime == "text/plain":
            return page_from_text(url, text)
        return page_from_html(url, text, self._markers)
