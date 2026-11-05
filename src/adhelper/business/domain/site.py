"""Разбор сайта в черновик профиля: что пропускаем в сеть и что оставляем из ответа модели.

Текст сайта и ответ модели — данные. Факт остаётся, только если его цитата дословно есть
на скачанной странице, а значение следует из цитаты. Решение о факте принимает владелец.
"""

import ipaddress
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

MAX_URL = 300
MAX_PRICE = 10_000_000
ALLOWED_PORTS = frozenset({None, 80, 443})
# Имена, которые ведут во внутреннюю сеть без всякого DNS.
PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


@dataclass(frozen=True)
class SiteRules:
    version: str
    max_pages: int
    max_page_bytes: int
    max_redirects: int
    timeout_seconds: int
    # Сколько символов всех страниц уходит модели: больше — дороже и не точнее.
    max_text_chars: int
    min_text_chars: int
    max_tokens: int
    max_reads_per_day: int
    max_guest_reads_per_address: int
    max_guest_reads_per_day: int
    max_attempts: int
    max_services: int
    max_features: int
    # Ссылки с такими словами ведут на страницы цен, услуг и контактов.
    link_words: tuple[str, ...]
    system_prompt: str


class UrlProblem(StrEnum):
    EMPTY = "empty"
    TOO_LONG = "too_long"
    BAD_SCHEME = "bad_scheme"
    CREDENTIALS = "credentials"
    BAD_PORT = "bad_port"
    NO_DOMAIN = "no_domain"
    PRIVATE = "private"


class FactField(StrEnum):
    NICHE = "niche"
    ADDRESS = "address"
    SERVICE = "service"
    FEATURE = "feature"


class FactProblem(StrEnum):
    MALFORMED = "malformed"
    UNKNOWN_FIELD = "unknown_field"
    EMPTY = "empty"
    TOO_LONG = "too_long"
    QUOTE_NOT_ON_PAGE = "quote_not_on_page"
    VALUE_NOT_IN_QUOTE = "value_not_in_quote"
    PRICE_NOT_IN_QUOTE = "price_not_in_quote"
    DUPLICATE = "duplicate"
    TOO_MANY = "too_many"


# Длины совпадают с полями профиля, иначе подтверждённый черновик не сохранится.
MAX_VALUE = {
    FactField.NICHE: 100,
    FactField.ADDRESS: 300,
    FactField.SERVICE: 100,
    FactField.FEATURE: 200,
}
MAX_QUOTE = 400
SINGLE = frozenset({FactField.NICHE, FactField.ADDRESS})


@dataclass(frozen=True)
class Page:
    url: str
    text: str


@dataclass(frozen=True)
class SiteFact:
    field: FactField
    value: str
    quote: str
    page: str
    price: int | None = None


def is_public_address(raw: str) -> bool:
    """Адрес из интернета, а не внутренней сети, облачных метаданных или служебных диапазонов."""
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def _host_problem(host: str) -> UrlProblem | None:
    if host == "localhost" or host.endswith(PRIVATE_SUFFIXES):
        return UrlProblem.PRIVATE
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None if "." in host else UrlProblem.NO_DOMAIN
    return None if is_public_address(host) else UrlProblem.PRIVATE


def check_url(raw: str, rules: SiteRules) -> tuple[str, UrlProblem | None]:
    """Адрес сайта, как его ввёл владелец, в виде для скачивания. DNS проверяет адаптер."""
    url = raw.strip()
    if not url:
        return "", UrlProblem.EMPTY
    if len(url) > MAX_URL:
        return "", UrlProblem.TOO_LONG
    if "://" not in url and not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.IGNORECASE):
        url = f"https://{url}"
    elif "://" not in url:
        return "", UrlProblem.BAD_SCHEME
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "", UrlProblem.NO_DOMAIN
    if parts.scheme.lower() not in ("http", "https"):
        return "", UrlProblem.BAD_SCHEME
    if parts.username is not None or parts.password is not None:
        return "", UrlProblem.CREDENTIALS
    if port not in ALLOWED_PORTS:
        return "", UrlProblem.BAD_PORT
    host = (parts.hostname or "").rstrip(".")
    if not host:
        return "", UrlProblem.NO_DOMAIN
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return "", UrlProblem.NO_DOMAIN
    problem = _host_problem(host)
    if problem is not None:
        return "", problem
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    normalized = urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))
    if len(normalized) > MAX_URL:
        return "", UrlProblem.TOO_LONG
    return normalized, None


_QUOTES = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "ё": "е"})


def normalize(text: str) -> str:
    """Сравнение без различий в регистре, кавычках, тире, неразрывных пробелах и «ё»."""
    text = text.casefold().translate(_QUOTES)
    text = re.sub(r"[‐-―−]", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    # «1 500» и «1500» — одно число.
    return re.sub(r"(?<=\d) (?=\d{3}(?!\d))", "", text)


def _has_number(text: str, number: int) -> bool:
    return re.search(rf"(?<!\d){number}(?!\d)", text) is not None


def _common_prefix(a: str, b: str) -> int:
    size = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        size += 1
    return size


def _word_matches(word: str, words: Sequence[str]) -> bool:
    # Числа и короткие слова совпадают точно, у длинных допускаем другое окончание:
    # «подология» в цитате «предлагаем подологию».
    if any(ch.isdigit() for ch in word) or len(word) <= 4:
        return word in words
    need = max(4, len(word) - 3)
    return any(_common_prefix(word, other) >= need for other in words)


def follows_from(value: str, quote: str) -> bool:
    """Значение взято из цитаты: дословно или теми же словами в другом падеже."""
    if value in quote:
        return True
    words = re.findall(r"\w+", quote)
    return all(_word_matches(word, words) for word in re.findall(r"\w+", value))


def _digits(text: str) -> list[str]:
    return re.findall(r"\d+", text)


def _check_one(raw: object, pages: Sequence[tuple[Page, str]]) -> SiteFact | FactProblem:
    if not isinstance(raw, Mapping):
        return FactProblem.MALFORMED
    field, value, quote, price = (raw.get(k) for k in ("field", "value", "quote", "price"))
    if not isinstance(value, str) or not isinstance(quote, str):
        return FactProblem.MALFORMED
    if price is not None and (not isinstance(price, int) or isinstance(price, bool)):
        return FactProblem.MALFORMED
    if not isinstance(field, str):
        return FactProblem.UNKNOWN_FIELD
    try:
        kind = FactField(field)
    except ValueError:
        return FactProblem.UNKNOWN_FIELD
    value, quote = value.strip(), quote.strip()
    if not value or not quote:
        return FactProblem.EMPTY
    if len(value) > MAX_VALUE[kind] or len(quote) > MAX_QUOTE:
        return FactProblem.TOO_LONG
    n_value, n_quote = normalize(value), normalize(quote)
    page = next((p for p, text in pages if n_quote in text), None)
    if page is None:
        return FactProblem.QUOTE_NOT_ON_PAGE
    if kind is FactField.NICHE:
        # Нишу модель называет своими словами, но чисел из воздуха в ней быть не может.
        if any(not _has_number(n_quote, int(d)) for d in _digits(n_value)):
            return FactProblem.VALUE_NOT_IN_QUOTE
    elif not follows_from(n_value, n_quote):
        return FactProblem.VALUE_NOT_IN_QUOTE
    if price is not None:
        if kind is not FactField.SERVICE or not 0 <= price <= MAX_PRICE:
            return FactProblem.MALFORMED
        if not _has_number(n_quote, price):
            return FactProblem.PRICE_NOT_IN_QUOTE
    return SiteFact(kind, value, quote, page.url, price)


def check_facts(
    raw: Sequence[object], pages: Sequence[Page], rules: SiteRules
) -> tuple[list[SiteFact], Counter[FactProblem]]:
    """Оставляет только факты с цитатой со страницы. Порядок модели сохраняется."""
    normalized = [(page, normalize(page.text)) for page in pages]
    limits = {FactField.SERVICE: rules.max_services, FactField.FEATURE: rules.max_features}
    kept: list[SiteFact] = []
    seen: set[tuple[FactField, str]] = set()
    problems: Counter[FactProblem] = Counter()
    for item in raw:
        result = _check_one(item, normalized)
        if isinstance(result, FactProblem):
            problems[result] += 1
            continue
        key = (result.field, "" if result.field in SINGLE else normalize(result.value))
        if key in seen:
            problems[FactProblem.DUPLICATE] += 1
            continue
        if sum(f.field is result.field for f in kept) >= limits.get(result.field, 1):
            problems[FactProblem.TOO_MANY] += 1
            continue
        seen.add(key)
        kept.append(result)
    return kept, problems


class ReadStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class ReadProblem(StrEnum):
    BAD_URL = "bad_url"
    PRIVATE = "private"
    UNREACHABLE = "unreachable"
    HTTP_ERROR = "http_error"
    NOT_HTML = "not_html"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    EMPTY_PAGE = "empty_page"
    NO_MODEL = "no_model"
    MODEL_UNAVAILABLE = "model_unavailable"


@dataclass(frozen=True)
class SiteRead:
    status: ReadStatus
    problem: ReadProblem | None = None
    facts: tuple[SiteFact, ...] = ()
    # Сколько ответов модели код отбросил и почему: для замера извлекателя.
    dropped: Mapping[str, int] = field(default_factory=dict)
    pages: tuple[str, ...] = ()
    tokens: int = 0


@dataclass(frozen=True)
class StoredRead:
    id: int
    url: str
    status: ReadStatus
    problem: str | None
    facts: tuple[SiteFact, ...]
    pages: tuple[str, ...]
