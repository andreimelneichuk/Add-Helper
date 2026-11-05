"""Чтение сайта браузером: текст после скриптов и сеть браузера только через защиту.

Настоящий Chromium (make setup ставит его) и стенды на 127.0.0.1 и [::1].
"""

import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from adhelper.business.adapters.browser import ChromiumRenderer
from adhelper.business.adapters.fetcher import UrlCheck
from adhelper.business.adapters.html_text import page_from_html
from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.domain.ports import FetchProblem, SiteUnavailable
from adhelper.business.domain.site import SiteRules, UrlProblem

RULES = load_site_rules("site-2")
assert RULES.render is not None
RENDER = replace(RULES.render, timeout_seconds=3)
FAST = replace(RULES, render=RENDER)

SPA = """<html><head><title>Ноготок</title></head><body><div id=app></div>
<details><summary>Прайс</summary><p>Педикюр — 2 000 ₽</p></details>
<img src="/photo.jpg">
<script src="/app.js"></script></body></html>"""
APP_JS = """
document.getElementById('app').innerHTML =
  '<h1>Маникюр с покрытием — 1 500 ₽</h1><a href="/price">Цены</a>';
"""
LEAK = """<html><body><p>Ноготок</p><p id=out>пусто</p>
<iframe src="SECRET"></iframe><script>
try { new WebSocket('WS'); } catch (e) {}
fetch('SECRET').then(r => r.text()).then(t => document.getElementById('out').textContent = t)
  .catch(() => document.getElementById('out').textContent = 'отказ');
</script></body></html>"""
HANGS = """<html><body><p>Ноготок, маникюр 1 500 ₽</p><script>
setInterval(() => fetch('/slow?' + Math.random()).catch(() => {}), 200);
</script></body></html>"""
COUNTER = """<html><body><p>Ноготок</p>
<script src="http://localhost:PORT/tag.js"></script></body></html>"""
GO_INSIDE = """<html><body><p>Ноготок</p><script>
location.href = 'http://localhost:PORT/';
</script></body></html>"""


def serve(
    host: str, family: socket.AddressFamily, pages: dict[str, tuple[str, str]], hits: list[str]
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            path = self.path.split("?")[0]
            if path == "/slow":
                time.sleep(2)
            mime, body = pages.get(path, ("text/plain", "нет"))
            data = body.encode()
            self.send_response(200 if path in pages or path == "/slow" else 404)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            # Повторный заголовок, как у Tilda: раньше из-за него страница не открывалась.
            self.send_header("Cache-Control", "max-age=0")
            self.send_header("Cache-Control", "public")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:
            return

    class Server(ThreadingHTTPServer):
        address_family = family
        daemon_threads = True

    server = Server((host, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def inside() -> Iterator[tuple[str, list[str]]]:
    """Стенд «внутренней сети» на [::1]: браузеру туда нельзя."""
    hits: list[str] = []
    server = serve("::1", socket.AF_INET6, {"/secret": ("text/plain", "ключ-от-базы")}, hits)
    yield f"http://[::1]:{server.server_port}", hits
    server.shutdown()


@pytest.fixture(scope="module")
def site(inside: tuple[str, list[str]]) -> Iterator[tuple[str, list[str]]]:
    hits: list[str] = []
    pages: dict[str, tuple[str, str]] = {
        "/": ("text/html", SPA),
        "/app.js": ("application/javascript", APP_JS),
        "/photo.jpg": ("image/jpeg", "jpeg"),
        "/leak": (
            "text/html",
            LEAK.replace("SECRET", f"{inside[0]}/secret").replace(
                "WS", inside[0].replace("http", "ws") + "/ws"
            ),
        ),
        "/hangs": ("text/html", HANGS),
        "/tag.js": ("application/javascript", "document.body.append('счётчик')"),
    }
    server = serve("127.0.0.1", socket.AF_INET, pages, hits)
    port = server.server_port
    pages["/counter"] = ("text/html", COUNTER.replace("PORT", str(port)))
    pages["/go-inside"] = ("text/html", GO_INSIDE.replace("PORT", str(port)))
    yield f"http://127.0.0.1:{port}", hits
    server.shutdown()


def any_url(url: str, rules: SiteRules) -> tuple[str, UrlProblem | None]:
    return (url, None) if url.startswith("http") else ("", UrlProblem.BAD_SCHEME)


def no_localhost(url: str, rules: SiteRules) -> tuple[str, UrlProblem | None]:
    # Имя localhost внутреннее уже по строке; 127.0.0.1 стенда разрешаем явно.
    if "//localhost" in url:
        return "", UrlProblem.PRIVATE
    return any_url(url, rules)


def stand_only(address: str) -> bool:
    """Сеть стенда — 127.0.0.1. Всё остальное, и [::1] тоже, считаем внутренним."""
    return address == "127.0.0.1"


def renderer(rules: SiteRules = FAST, url_check: UrlCheck = any_url) -> ChromiumRenderer:
    return ChromiumRenderer(rules, allow_address=stand_only, url_check=url_check)


async def test_text_drawn_by_script_and_hidden_price_are_read(site: tuple[str, list[str]]) -> None:
    base, hits = site
    async with renderer() as browser:
        page = await browser.render(f"{base}/")
    assert "Маникюр с покрытием — 1 500 ₽" in page.text
    # Свёрнутый прайс тоже текст страницы, хотя на экране его не видно.
    assert "Педикюр — 2 000 ₽" in page.text
    assert [link.url for link in page.links] == [f"{base}/price"]
    # Картинки тексту не нужны: браузер их не скачивает.
    assert "/photo.jpg" not in hits


async def test_script_cannot_reach_internal_network(
    site: tuple[str, list[str]], inside: tuple[str, list[str]]
) -> None:
    base, _ = site
    async with renderer() as browser:
        page = await browser.render(f"{base}/leak")
    assert "ключ-от-базы" not in page.text
    assert "отказ" in page.text
    # Ни запрос скрипта, ни фрейм, ни WebSocket не дошли даже до соединения.
    assert inside[1] == []


async def test_page_cannot_navigate_inside(site: tuple[str, list[str]]) -> None:
    base, _ = site
    async with renderer(url_check=no_localhost) as browser:
        page = await browser.render(f"{base}/go-inside")
    # Переход во внутреннюю сеть отменён, остаётся текст страницы владельца.
    assert page.url == f"{base}/go-inside"
    assert "Ноготок" in page.text


async def test_internal_address_is_refused(inside: tuple[str, list[str]]) -> None:
    async with renderer() as browser:
        with pytest.raises(SiteUnavailable) as caught:
            await browser.render(f"{inside[0]}/secret")
    assert caught.value.problem is FetchProblem.PRIVATE
    assert inside[1] == []


async def test_counters_are_not_loaded(site: tuple[str, list[str]]) -> None:
    base, hits = site
    rules = replace(FAST, render=replace(RENDER, blocked_hosts=("localhost",)))
    async with renderer(rules) as browser:
        page = await browser.render(f"{base}/counter")
    # Визит сервиса не должен попадать в статистику владельца.
    assert "счётчик" not in page.text
    assert "/tag.js" not in hits


async def test_page_that_never_goes_quiet_is_read_within_the_timeout(
    site: tuple[str, list[str]],
) -> None:
    base, _ = site
    async with renderer() as browser:
        await browser.render(f"{base}/")  # Запуск браузера в замер не входит.
        started = time.monotonic()
        page = await browser.render(f"{base}/hangs")
        elapsed = time.monotonic() - started
    assert elapsed < RENDER.timeout_seconds + 1.5
    assert "маникюр 1 500 ₽" in page.text


def test_site_builders_are_found_in_markup() -> None:
    page = page_from_html(
        "https://nogti.ru/",
        "<script src='https://static.tildacdn.com/js/tilda-scripts.min.js'></script>"
        "<div class='t-records'>Маникюр</div>",
        markers=("tilda", "taplink"),
    )
    assert page.builders == ("tilda",)
