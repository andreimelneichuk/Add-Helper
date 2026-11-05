"""Чтение сайта в Chromium, когда текст рисует скрипт (Tilda, Taplink, виджеты записи).

Своей сети у браузера нет. Каждый его запрос перехватывается и выполняется защищённым
клиентом из `fetcher.py`: строковая проверка адреса и проверка каждого IP при соединении
(docs/02, SSRF). То, что перехват не видит (WebSocket, WebRTC, DNS), упирается в
неработающий прокси без исключений для локальных адресов и в отключённый DNS браузера.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from types import TracebackType
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Request,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from adhelper.business.adapters.fetcher import (
    USER_AGENT,
    GuardedClient,
    TooBig,
    UrlCheck,
    checked_url,
)
from adhelper.business.adapters.html_text import page_from_html
from adhelper.business.domain.ports import FetchedPage, FetchProblem, SiteUnavailable
from adhelper.business.domain.site import RenderRules, SiteRules, check_url, is_public_address

logger = logging.getLogger(__name__)

# Порт 9 (discard) на loopback: соединение через «прокси» сразу отвергается.
DEAD_PROXY = "http://127.0.0.1:9"
LAUNCH_ARGS = [
    # По умолчанию Chromium ходит к localhost мимо прокси — эту лазейку закрываем.
    "--proxy-bypass-list=<-loopback>",
    "--host-resolver-rules=MAP * ~NOTFOUND",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--no-pings",
]
METHODS = frozenset({"GET", "HEAD", "POST"})
_SHADOW_DOM_SCRIPT = """() => {
    const shadowHtmls = [];
    const MAX_SHADOWS = 50;
    function findShadowRoots(root) {
        if (!root || shadowHtmls.length >= MAX_SHADOWS) return;
        try {
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
            let node = walker.currentNode;
            while (node && shadowHtmls.length < MAX_SHADOWS) {
                if (node.shadowRoot) {
                    try {
                        shadowHtmls.push(node.shadowRoot.innerHTML);
                        findShadowRoots(node.shadowRoot);
                    } catch {}
                }
                node = walker.nextNode();
            }
        } catch {}
    }
    if (document.body) {
        findShadowRoots(document.body);
    }
    return shadowHtmls;
}"""


async def browser_starts() -> bool:
    """Для `adhelper check`: браузер установлен и запускается с нашими параметрами."""
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True, args=LAUNCH_ARGS, proxy={"server": DEAD_PROXY}
            )
            await browser.close()
    except PlaywrightError:
        return False
    return True


class _Budget:
    def __init__(self, rules: RenderRules) -> None:
        self.requests = rules.max_requests
        self.bytes = rules.max_total_bytes
        # Почему не открылась сама страница: от этого зависит ответ владельцу.
        self.document_problem: FetchProblem | None = None
        # Текст снят: запоздавшим запросам отвечать уже некому.
        self.closed = False


def _blocked_host(url: str, hosts: tuple[str, ...]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == blocked or host.endswith(f".{blocked}") for blocked in hosts)


class ChromiumRenderer:
    """Один браузер на прогон воркера, отдельный чистый контекст на каждую страницу."""

    def __init__(
        self,
        rules: SiteRules,
        allow_address: Callable[[str], bool] = is_public_address,
        url_check: UrlCheck = check_url,
    ) -> None:
        if rules.render is None:
            raise ValueError(f"В правилах {rules.version} чтения браузером нет")
        self._rules = rules
        self._render = rules.render
        self._url_check = url_check
        self._client = GuardedClient(rules, allow_address, url_check)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "ChromiumRenderer":
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
            if self._playwright is not None:
                await self._playwright.stop()
        finally:
            self._browser = self._playwright = None
            await self._client.aclose()

    async def _launch(self) -> Browser:
        # Браузер поднимаем, только когда он понадобился: чаще хватает скачивания.
        async with self._lock:
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True, args=LAUNCH_ARGS, proxy={"server": DEAD_PROXY}
                )
            return self._browser

    async def render(self, url: str) -> FetchedPage:
        target = checked_url(url, self._rules, self._url_check)
        try:
            browser = await self._launch()
        except PlaywrightError as error:
            logger.error("Браузер не запустился: %s", type(error).__name__)
            raise SiteUnavailable(FetchProblem.UNREACHABLE) from None
        context = await browser.new_context(
            # Представляемся так же, как скачивание: сервис читает сайт по просьбе владельца.
            user_agent=USER_AGENT,
            service_workers="block",
            accept_downloads=False,
            locale="ru-RU",
        )
        try:
            # Запас сверх ожидания — на снятие текста; дольше страница не держит воркер.
            async with asyncio.timeout(self._render.timeout_seconds + 2):
                return await self._read(context, target)
        except TimeoutError:
            raise SiteUnavailable(FetchProblem.UNREACHABLE) from None
        finally:
            await context.unroute_all(behavior="ignoreErrors")
            await context.close()

    async def _read(self, context: BrowserContext, url: str) -> FetchedPage:
        budget = _Budget(self._render)
        page = await context.new_page()

        running: set[asyncio.Task[object]] = set()

        async def route(route: Route) -> None:
            task = asyncio.current_task()
            if task is not None:
                running.add(task)
            try:
                await self._route(route, page, budget)
            finally:
                running.discard(task)

        await context.route("**/*", route)
        try:
            return await self._open(page, url, budget)
        finally:
            budget.closed = True
            # Запоздавшие запросы страницы больше не нужны: не оставляем их висеть в воркере.
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)

    async def _open(self, page: Page, url: str, budget: _Budget) -> FetchedPage:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._render.timeout_seconds
        try:
            # Ждём только ответа на документ: остальное — в пределах срока, без отказа.
            response = await page.goto(
                url, wait_until="commit", timeout=self._render.timeout_seconds * 1000
            )
        except PlaywrightError:
            raise SiteUnavailable(budget.document_problem or FetchProblem.UNREACHABLE) from None
        if response is None or response.status != 200:
            raise SiteUnavailable(FetchProblem.HTTP_ERROR)
        # Тишина в сети — признак, что скрипты дорисовали страницу.
        for state in ("domcontentloaded", "networkidle"):
            left = deadline - loop.time()
            if left <= 0:
                break
            try:
                await page.wait_for_load_state(state, timeout=left * 1000)
            except PlaywrightTimeout:
                break
        return await self._snapshot(page)

    async def _snapshot(self, page: Page) -> FetchedPage:
        if urlsplit(page.url).scheme not in ("http", "https"):
            raise SiteUnavailable(FetchProblem.UNREACHABLE)  # Страница ошибки браузера.
        # Текст берём из DOM, а не из видимого: свёрнутые прайсы и окна записи тоже нужны.
        html = await _content(page)
        found = page_from_html(page.url, html[: self._rules.max_page_bytes], self._render.markers)
        texts = [found.text]
        # Сайты и виджеты в Shadow DOM (Salebot, Web Components): page.content() их не видит.
        try:
            shadows: list[str] = await page.evaluate(_SHADOW_DOM_SCRIPT)
        except PlaywrightError:
            shadows = []
        for shadow_html in shadows:
            inner = page_from_html(
                page.url, shadow_html[: self._rules.max_page_bytes], self._render.markers
            )
            if inner.text:
                texts.append(inner.text)
        for frame in page.frames[1 : self._render.max_frames + 1]:
            if frame.is_detached() or urlsplit(frame.url).scheme not in ("http", "https"):
                continue
            try:
                inner = page_from_html(
                    frame.url, (await frame.content())[: self._rules.max_page_bytes]
                )
            except PlaywrightError:
                continue
            if inner.text:
                texts.append(inner.text)
        return FetchedPage(found.url, "\n".join(texts), found.links, found.counters, found.builders)

    async def _route(self, route: Route, page: Page, budget: _Budget) -> None:
        request = route.request
        document = _is_document(request, page)
        refusal = self._refusal(request, budget)
        if refusal is not None:
            await self._refuse(route, page, budget, document, refusal)
            return
        budget.requests -= 1
        try:
            body = request.post_data_buffer
            resource = await self._client.request(
                request.method,
                request.url,
                await request.all_headers(),
                body,
                min(self._render.max_resource_bytes, budget.bytes),
            )
        except SiteUnavailable as error:
            if not budget.closed:
                await self._refuse(route, page, budget, document, error.problem)
            return
        except TooBig:
            if not budget.closed:
                await _abort(route)
            return
        if budget.closed:
            return
        budget.bytes -= len(resource.body)
        headers = _merged(resource.headers)
        try:
            await route.fulfill(status=resource.status, headers=headers, body=resource.body)
        except PlaywrightError:
            return  # Страница уже закрыта.

    async def _refuse(
        self, route: Route, page: Page, budget: _Budget, document: bool, problem: FetchProblem
    ) -> None:
        if document and urlsplit(page.url).scheme in ("http", "https"):
            # Скрипт уже открытой страницы уводит её: ответ 204 отменяет переход,
            # и страница владельца остаётся на месте вместо страницы ошибки.
            with contextlib.suppress(PlaywrightError):
                await route.fulfill(status=204, body=b"")
            return
        if document:
            budget.document_problem = problem
        await _abort(route)

    def _refusal(self, request: Request, budget: _Budget) -> FetchProblem | None:
        if urlsplit(request.url).scheme not in ("http", "https"):
            return FetchProblem.BAD_URL
        if request.method not in METHODS or request.resource_type in self._render.blocked_types:
            return FetchProblem.BAD_URL
        if _blocked_host(request.url, self._render.blocked_hosts):
            return FetchProblem.BAD_URL
        if budget.requests <= 0 or budget.bytes <= 0:
            return FetchProblem.UNREACHABLE
        return None


def _merged(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Повторы заголовка через запятую; только set-cookie — построчно, как ждёт Playwright.

    Перевод строки в другом заголовке Chromium считает ошибкой и не открывает страницу.
    """
    headers: dict[str, str] = {}
    for key, value in pairs:
        name = key.lower()
        if name in headers:
            joiner = "\n" if name == "set-cookie" else ", "
            headers[name] = f"{headers[name]}{joiner}{value}"
        else:
            headers[name] = value
    return headers


def _is_document(request: Request, page: Page) -> bool:
    try:
        return request.is_navigation_request() and request.frame == page.main_frame
    except PlaywrightError:
        return False


async def _abort(route: Route) -> None:
    try:
        await route.abort("blockedbyclient")
    except PlaywrightError:
        return


async def _content(page: Page) -> str:
    # Скрипт мог начать переход в тот же момент: тогда ждём и пробуем ещё раз.
    for _ in range(3):
        try:
            return await page.content()
        except PlaywrightError:
            await asyncio.sleep(0.2)
    raise SiteUnavailable(FetchProblem.UNREACHABLE)
