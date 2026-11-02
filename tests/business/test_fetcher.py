"""Скачивание сайта: лимиты, кодировки и запрет внутренней сети."""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from adhelper.business.adapters.fetcher import SafeFetcher
from adhelper.business.adapters.html_text import page_from_html
from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.domain.ports import FetchProblem, SiteUnavailable
from adhelper.business.domain.site import SiteRules, UrlProblem

RULES = load_site_rules()
PAGES: dict[str, tuple[int, dict[str, str], bytes]] = {
    "/": (
        200,
        {"Content-Type": "text/html; charset=utf-8"},
        (
            "<html><head><title>Ноготок</title><script>alert(1)</script></head>"
            "<body><h1>Маникюр</h1><p>Цена&nbsp;1 500 ₽</p>"
            "<a href='/price'>Цены</a></body></html>"
        ).encode(),
    ),
    "/old": (
        200,
        {"Content-Type": "text/html"},
        "<meta charset=windows-1251><p>Педикюр от 2000 руб.</p>".encode("cp1251"),
    ),
    "/go": (302, {"Location": "/"}, b""),
    "/loop": (302, {"Location": "/loop"}, b""),
    "/ftp": (302, {"Location": "ftp://example.ru/"}, b""),
    "/pdf": (200, {"Content-Type": "application/pdf"}, b"%PDF"),
    "/gone": (404, {"Content-Type": "text/html"}, b"no"),
    "/big": (200, {"Content-Type": "text/plain"}, b"a" * 3_000_000),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, headers, body = PAGES.get(self.path, (404, {}, b""))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def base() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def any_url(url: str, rules: SiteRules) -> tuple[str, UrlProblem | None]:
    return (url, None) if url.startswith("http") else ("", UrlProblem.BAD_SCHEME)


def local() -> SafeFetcher:
    """Сеть стенда — внутренняя, поэтому проверку адреса здесь разрешаем явно."""
    return SafeFetcher(RULES, allow_address=lambda _: True, url_check=any_url)


async def test_page_text_without_scripts_and_links(base: str) -> None:
    page = await local().fetch(f"{base}/")
    assert page.text.splitlines() == ["Ноготок", "Маникюр", "Цена 1 500 ₽", "Цены"]
    assert [(link.url, link.text) for link in page.links] == [(f"{base}/price", "Цены")]


async def test_old_encoding_and_redirect(base: str) -> None:
    assert (await local().fetch(f"{base}/old")).text == "Педикюр от 2000 руб."
    assert (await local().fetch(f"{base}/go")).text.startswith("Ноготок")


@pytest.mark.parametrize(
    ("path", "problem"),
    [
        ("/loop", FetchProblem.TOO_MANY_REDIRECTS),
        ("/ftp", FetchProblem.BAD_URL),
        ("/pdf", FetchProblem.NOT_HTML),
        ("/gone", FetchProblem.HTTP_ERROR),
    ],
)
async def test_refusals(base: str, path: str, problem: FetchProblem) -> None:
    with pytest.raises(SiteUnavailable) as caught:
        await local().fetch(f"{base}{path}")
    assert caught.value.problem is problem


async def test_big_page_is_cut_to_the_limit(base: str) -> None:
    page = await local().fetch(f"{base}/big")
    assert len(page.text) == RULES.max_page_bytes


async def test_internal_address_is_refused_at_connection(base: str) -> None:
    # Строковая проверка пропустила, но соединение с 127.0.0.1 запрещено сетевым слоем.
    fetcher = SafeFetcher(RULES, url_check=any_url)
    with pytest.raises(SiteUnavailable) as caught:
        await fetcher.fetch(f"{base}/")
    assert caught.value.problem is FetchProblem.PRIVATE


async def test_name_that_resolves_inside_is_refused() -> None:
    # Имя без IP в адресе: что оно внутреннее, видно только после DNS.
    fetcher = SafeFetcher(RULES, url_check=any_url)
    with pytest.raises(SiteUnavailable) as caught:
        await fetcher.fetch("http://localhost:9/")
    assert caught.value.problem is FetchProblem.PRIVATE


async def test_default_check_refuses_internal_urls() -> None:
    with pytest.raises(SiteUnavailable) as caught:
        await SafeFetcher(RULES).fetch("http://169.254.169.254/latest/meta-data")
    assert caught.value.problem is FetchProblem.PRIVATE


def test_html_cleaning_keeps_description_and_drops_hidden() -> None:
    page = page_from_html(
        "https://nogti.ru/",
        "<head><meta name='description' content='Студия у метро'></head>"
        "<body><noscript>Включите JS</noscript><style>p{}</style>"
        "<ul><li>Маникюр</li><li>Маникюр</li><li>Педикюр</li></ul>"
        "<a href='tel:+7900'>позвонить</a><a href='https://vk.com/x#a'>VK</a></body>",
    )
    assert page.text.splitlines() == ["Студия у метро", "Маникюр", "Педикюр", "позвонитьVK"]
    assert [link.url for link in page.links] == ["https://vk.com/x"]
