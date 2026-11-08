"""Тонкий асинхронный клиент Директ API (docs/03-integrations.md «Клиент»).

Знает только транспорт: адреса и версии сервисов, заголовки, баллы, разбор ошибок и повтор
того, что заведомо не применилось. Повтор изменения после сбоя связи решает журнал: он
начинает с чтения, потому что многие методы Директа не идемпотентны.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from adhelper.budget.domain.ports import PlatformAuthError, PlatformUnavailable

logger = logging.getLogger(__name__)

PRODUCTION_URL = "https://api.direct.yandex.com"
SANDBOX_URL = "https://api-sandbox.direct.yandex.com"
# Баланс общего счёта есть только в Live v4 (AccountManagement), в v5 его нет.
PRODUCTION_LIVE_URL = "https://api.direct.yandex.ru/live/v4/json/"
SANDBOX_LIVE_URL = "https://api-sandbox.direct.yandex.ru/live/v4/json/"

# Единая перфоманс-кампания и её группы работают только через v501 (docs/00-research.md §2).
SERVICE_VERSIONS: Mapping[str, str] = {
    "campaigns": "v501",
    "adgroups": "v501",
    "ads": "v501",
    # Картинки есть только в v5: /json/v5/adimages.
    "adimages": "v5",
    "keywords": "v5",
    "clients": "v5",
    "dictionaries": "v5",
    "reports": "v5",
}

# Коды ошибок уровня запроса (справочник ошибок Директа).
AUTH_ERRORS = frozenset({53, 54, 58, 513})
POINTS_ERRORS = frozenset({152})
# Сервер не взялся за работу: занят, лимит соединений, нет сервера авторизации.
# Такой отказ повторяем и для изменений.
NOT_STARTED_ERRORS = frozenset({52, 56, 506})
# Внутренние ошибки Директа: чтение повторяем, изменение — нет, его доводит журнал после чтения.
SERVER_ERRORS = frozenset({1000, 1001})
RETRYABLE_ERRORS = NOT_STARTED_ERRORS | SERVER_ERRORS
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
# Методы без изменений: их можно повторять вслепую.
READ_METHODS = frozenset({"get"})

# Токен кабинета по логину; токены свои у каждого бизнеса, поэтому источник у адаптера.
TokenSource = Callable[[str], Awaitable[str]]
Sleep = Callable[[float], Awaitable[None]]


class DirectError(PlatformUnavailable):
    """Директ не выполнил запрос. Для журнала это сбой площадки: он перечитает и повторит."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(f"direct {code}: {message}" if code is not None else message)
        self.code = code


class DirectAuthError(DirectError, PlatformAuthError):
    """Токен недействителен, нет прав или логин не подключён к Директу: нужен владелец."""


class DirectPointsExhausted(DirectError):
    """Кончились баллы API. Действие не выполнено, повтор после пополнения."""


class DirectRejected(DirectError):
    """Площадка отказала в действии над объектом; причина — данные площадки, не инструкция."""


class DirectGeoRegionAmbiguous(DirectError):
    """В справочнике совпало несколько городов России, нельзя выбирать регион наугад."""


@dataclass(frozen=True)
class Units:
    """Заголовок `Units`: потрачено запросом / осталось / суточный лимит."""

    spent: int
    rest: int
    limit: int

    @staticmethod
    def parse(raw: str | None) -> "Units | None":
        if not raw:
            return None
        parts = raw.split("/")
        if len(parts) != 3 or not all(part.strip().isdigit() for part in parts):
            return None
        spent, rest, limit = (int(part) for part in parts)
        return Units(spent, rest, limit)


def rubles_to_micros(amount: str) -> int:
    """Live v4 отдаёт сумму строкой в рублях; храним целые микроединицы, без округления."""
    micros = Decimal(amount) * 1_000_000
    if micros != micros.to_integral_value():
        raise ValueError("Сумма точнее микроединицы")
    return int(micros)


def check_results(results: Sequence[Mapping[str, Any]]) -> list[int]:
    """Номера из `AddResults`/`UpdateResults`; ошибка по объекту — отказ площадки."""
    ids: list[int] = []
    for result in results:
        errors = result.get("Errors") or []
        if errors:
            first = errors[0]
            message = str(first.get("Message", ""))
            if first.get("Details"):
                message += f": {first['Details']}"
            raise DirectRejected(int(first.get("Code", 0)), message)
        if "Id" in result:
            ids.append(int(result["Id"]))
        elif "ClientId" in result:
            ids.append(int(result["ClientId"]))
    return ids


class DirectHttpClient:
    """Один клиент на процесс: ограничивает одновременные запросы и считает баллы по логину."""

    def __init__(
        self,
        base_url: str = PRODUCTION_URL,
        live_url: str = PRODUCTION_LIVE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int = 2,
        attempts: int = 3,
        backoff: float = 1.0,
        sleep: Sleep = asyncio.sleep,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._live_url = live_url
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout)
        # Директ разрешает до пяти одновременных запросов на рекламодателя, у нас два.
        self._slots = asyncio.Semaphore(concurrency)
        self._attempts = attempts
        self._backoff = backoff
        self._sleep = sleep
        self.units: dict[str, Units] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    def _url(self, service: str) -> str:
        return f"{self._base_url}/json/{SERVICE_VERSIONS[service]}/{service}"

    def _headers(
        self, account: str, token: str, extra: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept-Language": "ru",
        }
        if account:
            headers["Client-Login"] = account
        headers.update(extra or {})
        return headers

    async def _post(
        self,
        account: str,
        url: str,
        body: Mapping[str, Any],
        headers: Mapping[str, str],
        read_only: bool,
    ) -> httpx.Response:
        """Чтение повторяет при 5xx/429. Изменение — только если соединения не было: `add`
        мог примениться до потери ответа, повтор вслепую создал бы второй объект."""
        last: Exception | None = None
        for attempt in range(self._attempts):
            if attempt:
                await self._sleep(self._backoff * 2 ** (attempt - 1))
            try:
                async with self._slots:
                    response = await self._http.post(url, json=body, headers=headers)
            except httpx.TimeoutException as error:
                # Тайм-аут после отправки: применилось ли, неизвестно. Решает журнал после чтения.
                raise DirectError(None, f"timeout {url}") from error
            except httpx.TransportError as error:
                last = DirectError(None, f"{type(error).__name__} {url}")
                if isinstance(error, httpx.ConnectError):
                    continue
                raise last from error
            if response.status_code in RETRYABLE_STATUSES:
                last = DirectError(None, f"http {response.status_code} {url}")
                if read_only:
                    continue
                raise last
            units = Units.parse(response.headers.get("Units"))
            if units is not None:
                self.units[account] = units
            logger.info(
                "direct %s %s status=%s units=%s request=%s",
                url.rsplit("/", 1)[-1] or "live",
                body.get("method"),
                response.status_code,
                response.headers.get("Units"),
                response.headers.get("RequestId"),
            )
            return response
        assert last is not None
        raise last

    async def call(
        self, account: str, token: str, service: str, method: str, params: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Вызов JSON-сервиса v5/v501. Возвращает `result`."""
        body = {"method": method, "params": params}
        read_only = method in READ_METHODS
        retryable = RETRYABLE_ERRORS if read_only else NOT_STARTED_ERRORS
        for attempt in range(self._attempts):
            response = await self._post(
                account, self._url(service), body, self._headers(account, token), read_only
            )
            payload = _json(response)
            error = payload.get("error")
            if error is None:
                result = payload.get("result")
                if not isinstance(result, Mapping):
                    raise DirectError(None, f"no result in {service}.{method}")
                return result
            code = _code(error)
            if code in retryable and attempt + 1 < self._attempts:
                await self._sleep(self._backoff * 2**attempt)
                continue
            raise _request_error(code, error)
        raise DirectError(None, f"{service}.{method} retries exhausted")

    async def token_owner(self, token: str) -> str | None:
        """Логин владельца токена: Директ отдаёт его сам, без права `login:info`."""
        result = await self.call("", token, "clients", "get", {"FieldNames": ["Login"]})
        clients = result.get("Clients")
        if isinstance(clients, list) and clients and isinstance(clients[0], Mapping):
            login = clients[0].get("Login")
            return login if isinstance(login, str) and login else None
        return None

    async def live(self, account: str, token: str, method: str, param: Mapping[str, Any]) -> Any:
        """Live v4: токен идёт в теле. Ответ — `data`."""
        body = {"method": method, "token": token, "locale": "ru", "param": param}
        response = await self._post(
            account, self._live_url, body, {}, read_only=param.get("Action") == "Get"
        )
        payload = _json(response)
        if "error_code" in payload:
            code = _int(payload.get("error_code"))
            raise _request_error(code, payload)
        return payload.get("data")

    async def report(
        self, account: str, token: str, definition: Mapping[str, Any], max_waits: int = 10
    ) -> str:
        """Отчёт TSV без строки названия и итога. 201/202 — отчёт в очереди, ждём `retryIn`."""
        headers = self._headers(
            account,
            token,
            {
                "processingMode": "auto",
                "returnMoneyInMicros": "true",
                "skipReportHeader": "true",
                "skipReportSummary": "true",
            },
        )
        body = {"params": definition}
        for _ in range(max_waits):
            response = await self._post(account, self._url("reports"), body, headers, True)
            if response.status_code == 200:
                return response.text
            if response.status_code in (201, 202):
                await self._sleep(float(response.headers.get("retryIn", "5")))
                continue
            payload = _json(response)
            error = payload.get("error", payload)
            raise _request_error(_code(error), error)
        raise DirectError(None, "report is not ready")


def _json(response: httpx.Response) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except ValueError as error:
        raise DirectError(None, f"not json, http {response.status_code}") from error
    if not isinstance(payload, Mapping):
        raise DirectError(None, "unexpected payload")
    return payload


def _int(raw: Any) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _code(error: Any) -> int | None:
    return _int(error.get("error_code")) if isinstance(error, Mapping) else None


def _request_error(code: int | None, error: Mapping[str, Any]) -> DirectError:
    # Текст ошибки — данные площадки. Токена в нём нет, реквизитов мы туда не передаём.
    message = str(error.get("error_string", "")) + ": " + str(error.get("error_detail", ""))
    if code in AUTH_ERRORS:
        return DirectAuthError(code, message)
    if code in POINTS_ERRORS:
        return DirectPointsExhausted(code, message)
    if code in RETRYABLE_ERRORS:
        return DirectError(code, message)
    return DirectRejected(code, message)
