"""Сценарий разбора сайта на поддельных сайте и модели."""

import asyncio
import json
from dataclasses import replace

from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.application.site import ReadProblem, ReadStatus, read_site
from adhelper.business.domain.ports import FetchedPage, FetchProblem, Link, SiteUnavailable
from adhelper.creatives.domain.model import ModelReply, ModelUnavailable

RULES = load_site_rules()
HOME = FetchedPage(
    "https://nogti.ru/",
    "Студия маникюра «Ноготок»\nАдрес: г. Казань, ул. Баумана, 12",
    (
        Link("https://nogti.ru/price", "Цены"),
        Link("https://nogti.ru/blog", "Блог"),
        Link("https://other.ru/uslugi", "Услуги партнёров"),
        Link("https://nogti.ru/uslugi", "Наши услуги"),
        Link("https://nogti.ru/kontakty", "Контакты"),
    ),
)
PRICE = FetchedPage("https://nogti.ru/price", "Маникюр с покрытием — 1 500 ₽")


class Site:
    def __init__(self, *pages: FetchedPage, broken: set[str] | None = None) -> None:
        self.pages = {page.url: page for page in pages}
        self.broken = broken or set()
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchedPage:
        self.calls.append(url)
        if url in self.broken or url not in self.pages:
            raise SiteUnavailable(FetchProblem.UNREACHABLE)
        return self.pages[url]


class Model:
    def __init__(self, reply: object = None, fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail
        self.prompts: list[tuple[str, str]] = []

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        self.prompts.append((system, user))
        if self.fail:
            raise ModelUnavailable("timeout")
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return ModelReply(text, 321)


GOOD = {
    "facts": [
        {"field": "niche", "value": "маникюр", "quote": "Студия маникюра «Ноготок»", "price": None},
        {
            "field": "service",
            "value": "Маникюр с покрытием",
            "quote": "Маникюр с покрытием — 1 500 ₽",
            "price": 1500,
        },
        {"field": "feature", "value": "Лучшие мастера", "quote": "Лучшие мастера", "price": None},
    ]
}


async def test_reads_home_and_price_pages_and_keeps_quoted_facts() -> None:
    site = Site(HOME, PRICE)
    model = Model(GOOD)
    result = await read_site("https://nogti.ru/", site, model, RULES)
    assert result.status is ReadStatus.READY
    # Главная, затем страницы того же сайта со словами из правил, не больше max_pages.
    assert site.calls == ["https://nogti.ru/", "https://nogti.ru/price", "https://nogti.ru/uslugi"]
    assert result.pages == ("https://nogti.ru/", "https://nogti.ru/price")
    assert [(f.field.value, f.value, f.price, f.page) for f in result.facts] == [
        ("niche", "маникюр", None, "https://nogti.ru/"),
        ("service", "Маникюр с покрытием", 1500, "https://nogti.ru/price"),
    ]
    assert result.dropped == {"quote_not_on_page": 1}
    assert result.tokens == 321
    system, user = model.prompts[0]
    assert system == RULES.system_prompt
    # Текст страниц передан как данные с адресом, а не как инструкция.
    assert "https://nogti.ru/price" in user and "1 500 ₽" in user


async def test_unreachable_home_page_fails_without_model() -> None:
    model = Model(GOOD)
    result = await read_site("https://nogti.ru/", Site(), model, RULES)
    assert (result.status, result.problem) == (ReadStatus.FAILED, ReadProblem.UNREACHABLE)
    assert model.prompts == []


async def test_empty_page_is_reported() -> None:
    result = await read_site(
        "https://nogti.ru/", Site(FetchedPage("https://nogti.ru/", "Загрузка")), Model(GOOD), RULES
    )
    assert result.problem is ReadProblem.EMPTY_PAGE


async def test_no_model_and_model_failure() -> None:
    no_model = await read_site("https://nogti.ru/", Site(HOME), None, RULES)
    assert no_model.problem is ReadProblem.NO_MODEL
    failed = await read_site("https://nogti.ru/", Site(HOME), Model(fail=True), RULES)
    assert failed.problem is ReadProblem.MODEL_UNAVAILABLE
    garbage = await read_site("https://nogti.ru/", Site(HOME), Model("не json"), RULES)
    assert garbage.problem is ReadProblem.MODEL_UNAVAILABLE


async def test_text_sent_to_model_is_limited_and_quotes_checked_against_it() -> None:
    long_home = FetchedPage("https://nogti.ru/", "Маникюр. " + "х" * 50_000 + " Педикюр 900 ₽")
    model = Model(
        {
            "facts": [
                {"field": "service", "value": "Педикюр", "quote": "Педикюр 900 ₽", "price": 900},
            ]
        }
    )
    result = await read_site("https://nogti.ru/", Site(long_home), model, RULES)
    assert len(model.prompts[0][1]) < RULES.max_text_chars + 1000
    # Модель не видела конец страницы, значит и цитата оттуда не принимается.
    assert result.facts == ()
    assert result.status is ReadStatus.READY


def test_every_fetch_problem_has_a_read_problem() -> None:
    # Ошибка скачивания переходит в ответ разбора без потерь: у экрана один словарь.
    assert {p.value for p in FetchProblem} <= {p.value for p in ReadProblem}


# Браузер (site-2): статический текст первым, браузер — когда его мало.
SPA = FetchedPage("https://nails.taplink.ru/", "Nails at Taplink", builders=("taplink",))
SPA_RENDERED = FetchedPage(
    "https://nails.taplink.ru/",
    "Nails at Taplink\nСтудия маникюра «Ноготок»\nМаникюр с покрытием — 1 500 ₽",
)


class Browser:
    def __init__(
        self, *pages: FetchedPage, fail: bool = False, delay: dict[str, float] | None = None
    ) -> None:
        self.pages = {page.url: page for page in pages}
        self.fail = fail
        self.delay = delay or {}
        self.calls: list[str] = []

    async def render(self, url: str) -> FetchedPage:
        self.calls.append(url)
        await asyncio.sleep(self.delay.get(url, 0))
        if self.fail or url not in self.pages:
            raise SiteUnavailable(FetchProblem.UNREACHABLE)
        return self.pages[url]


async def test_page_drawn_by_script_is_read_by_browser() -> None:
    browser = Browser(SPA_RENDERED)
    model = Model(GOOD)
    result = await read_site(SPA.url, Site(SPA), model, RULES, browser)
    assert browser.calls == [SPA.url]
    assert result.status is ReadStatus.READY
    # Цитаты сверяются с текстом из браузера: его видела модель.
    assert [(f.field.value, f.price) for f in result.facts] == [("niche", None), ("service", 1500)]
    assert "1 500 ₽" in model.prompts[0][1]


async def test_rich_static_page_with_prices_skips_browser() -> None:
    rich = FetchedPage("https://nogti.ru/", "Маникюр с покрытием — 1 500 ₽. " + "Студия. " * 300)
    browser = Browser(rich)
    await read_site(rich.url, Site(rich), Model(GOOD), RULES, browser)
    assert browser.calls == []


async def test_browser_failure_or_poorer_text_keeps_static_page() -> None:
    failed = await read_site(SPA.url, Site(SPA), Model(GOOD), RULES, Browser(fail=True))
    # Браузер не помог — честно говорим, что текста почти нет.
    assert failed.problem is ReadProblem.EMPTY_PAGE
    static = replace(SPA, text="Ноготок: маникюр и педикюр у метро, запись по телефону")
    model = Model({"facts": []})
    await read_site(SPA.url, Site(static), model, RULES, Browser(FetchedPage(SPA.url, "Загрузка")))
    assert static.text in model.prompts[0][1]


async def test_site_1_reads_never_open_browser() -> None:
    browser = Browser(SPA_RENDERED)
    await read_site(SPA.url, Site(SPA), Model(GOOD), load_site_rules("site-1"), browser)
    assert browser.calls == []


async def test_browser_gets_only_what_is_left_of_the_site_budget() -> None:
    assert RULES.render is not None
    rules = replace(RULES, render=replace(RULES.render, site_budget_seconds=0.3))
    price = FetchedPage("https://nogti.ru/price", "Цены: маникюр 1 500 ₽", builders=("tilda",))
    contacts = FetchedPage("https://nogti.ru/kontakty", "Казань, ул. Баумана, 12 — 1 500 ₽")
    home = replace(HOME, links=(Link(price.url, "Цены"), Link(contacts.url, "Контакты")))
    home = replace(home, text=home.text + " Маникюр 1 500 ₽ " + "Студия. " * 300)
    browser = Browser(replace(price, text=price.text + " и педикюр"), delay={price.url: 5})
    started = asyncio.get_running_loop().time()
    result = await read_site(home.url, Site(home, price, contacts), Model(GOOD), rules, browser)
    assert asyncio.get_running_loop().time() - started < 1
    # Браузер на странице цен не успел: она идёт в разбор со скачанным текстом.
    assert browser.calls == [price.url, contacts.url]
    assert result.pages == (home.url, price.url, contacts.url)


async def test_slow_browser_on_home_page_keeps_static_text() -> None:
    assert RULES.render is not None
    rules = replace(RULES, render=replace(RULES.render, site_budget_seconds=0.3))
    static = replace(SPA, text="Ноготок: маникюр и педикюр у метро, запись по телефону")
    model = Model({"facts": []})
    browser = Browser(SPA_RENDERED, delay={SPA.url: 5})
    result = await read_site(SPA.url, Site(static), model, rules, browser)
    # Браузер не успел к сроку — разбор идёт по скачанному тексту, а не падает.
    assert result.status is ReadStatus.READY
    assert static.text in model.prompts[0][1]
