"""Видимый текст страницы и её ссылки. Скрипты, стили и разметка до модели не доходят."""

import re
from collections.abc import Sequence
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from adhelper.business.domain.ports import FetchedPage, Link
from adhelper.business.domain.tag import find_counters

SKIP = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "head", "object"})
BLOCK = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "ul",
        "ol",
        "tr",
        "table",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "aside",
        "nav",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "dt",
        "dd",
        "blockquote",
        "address",
        "form",
        "figcaption",
        "hr",
        "td",
        "th",
        "option",
    }
)
VOID = frozenset({"br", "hr", "img", "meta", "link", "input", "source", "wbr", "area", "col"})
MAX_LINKS = 200


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.in_title = False
        self.title: list[str] = []
        self.description = ""
        self.parts: list[str] = []
        self.links: list[tuple[str, list[str]]] = []
        self.open_link: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = (values.get("name") or values.get("property") or "").lower()
            if name in ("description", "og:description") and not self.description:
                self.description = values.get("content", "")
        if tag in SKIP and tag not in VOID:
            self.skip_depth += 1
            return
        if tag in BLOCK:
            self.parts.append("\n")
        if tag == "a" and self.skip_depth == 0 and len(self.links) < MAX_LINKS:
            self.open_link = []
            self.links.append((values.get("href", ""), self.open_link))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag in SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self.open_link = None

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
            return
        if self.skip_depth:
            return
        self.parts.append(data)
        if self.open_link is not None:
            self.open_link.append(data)


def _clean(text: str) -> str:
    return re.sub(r"[ \t\r\f\v ​]+", " ", text).strip()


def _link(base: str, href: str) -> str | None:
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    parts = urlsplit(urljoin(base, href))
    if parts.scheme not in ("http", "https"):
        return None
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def find_builders(html: str, markers: Sequence[str]) -> tuple[str, ...]:
    """Следы конструкторов в разметке: адреса скриптов, классы, комментарии."""
    lowered = html.lower()
    return tuple(marker for marker in markers if marker in lowered)


def page_from_html(url: str, html: str, markers: Sequence[str] = ()) -> FetchedPage:
    reader = _Reader()
    reader.feed(html)
    reader.close()
    lines: list[str] = []
    for line in [_clean("".join(reader.title)), _clean(reader.description)]:
        if line:
            lines.append(line)
    for raw in "".join(reader.parts).split("\n"):
        line = _clean(raw)
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    links: list[Link] = []
    seen: set[str] = set()
    for href, text in reader.links:
        target = _link(url, href)
        if target is not None and target not in seen:
            seen.add(target)
            links.append(Link(target, _clean("".join(text))))
    return FetchedPage(
        url, "\n".join(lines), tuple(links), find_counters(html), find_builders(html, markers)
    )


def page_from_text(url: str, text: str) -> FetchedPage:
    lines = [_clean(line) for line in text.splitlines()]
    return FetchedPage(url, "\n".join(line for line in lines if line), ())
