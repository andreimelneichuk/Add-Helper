"""Картинки сайта владельца как кандидаты в фото для объявлений.

Берём только адреса из разметки страницы; решает владелец, а согласие на людей на фото
и права на снимок подтверждает он (docs/05-creatives.md §5). Скачивание идёт тем же защищённым
клиентом, что и чтение сайта.
"""

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from adhelper.business.adapters.fetcher import SafeFetcher
from adhelper.business.domain.ports import SiteUnavailable
from adhelper.business.domain.site import SiteRules

HTML_TYPES = ("text/html", "application/xhtml+xml")
IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")
MAX_CANDIDATES = 12
# Логотипы, значки и счётчики фото салона не заменяют.
SKIP = re.compile(r"(logo|icon|favicon|sprite|pixel|avatar|badge|banner-ad|/emoji)", re.I)
SRCSET_FIRST = re.compile(r"\s*([^\s,]+)")


class _Images(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v for k, v in attrs if v}
        if tag == "meta" and values.get("property") in ("og:image", "twitter:image"):
            self.found.append(values.get("content", ""))
        elif tag == "img":
            for key in ("src", "data-src", "data-original"):
                if key in values:
                    self.found.append(values[key])
            srcset = values.get("srcset") or values.get("data-srcset")
            if srcset and (match := SRCSET_FIRST.match(srcset)):
                self.found.append(match.group(1))
        elif tag == "source" and (match := SRCSET_FIRST.match(values.get("srcset", ""))):
            self.found.append(match.group(1))


def image_urls(html: str, base: str) -> list[str]:
    """Адреса картинок страницы по порядку, без повторов, значков и не-http."""
    parser = _Images()
    parser.feed(html)
    seen: set[str] = set()
    result: list[str] = []
    for raw in parser.found:
        url = urljoin(base, raw.strip())
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or url in seen:
            continue
        if parts.path.lower().endswith((".svg", ".ico")) or SKIP.search(url):
            continue
        seen.add(url)
        result.append(url)
    return result[:MAX_CANDIDATES]


async def site_image_urls(fetcher: SafeFetcher, url: str, rules: SiteRules) -> list[str]:
    final, _mime, body = await fetcher.get_bytes(url, "text/html", HTML_TYPES, rules.max_page_bytes)
    return image_urls(body.decode("utf-8", errors="replace"), final)


async def download_image(fetcher: SafeFetcher, url: str, max_bytes: int) -> bytes | None:
    try:
        _final, _mime, body = await fetcher.get_bytes(
            url, "image/jpeg,image/png,image/webp,image/gif", IMAGE_TYPES, max_bytes
        )
    except SiteUnavailable:
        return None
    return body


async def download_many(
    fetcher: SafeFetcher, urls: list[str], max_bytes: int, total_seconds: float = 20.0
) -> list[bytes | None]:
    """Картинки параллельно и в общий срок: что не успело, считается недоступным."""
    gate = asyncio.Semaphore(4)

    async def one(url: str) -> bytes | None:
        async with gate:
            return await download_image(fetcher, url, max_bytes)

    tasks = [asyncio.ensure_future(one(url)) for url in urls]
    await asyncio.wait(tasks, timeout=total_seconds)
    for task in tasks:
        if not task.done():
            task.cancel()
    return [
        t.result() if t.done() and not t.cancelled() and t.exception() is None else None
        for t in tasks
    ]
