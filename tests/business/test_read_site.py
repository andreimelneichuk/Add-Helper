"""Сценарий разбора сайта на поддельных сайте и модели."""

import json

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
