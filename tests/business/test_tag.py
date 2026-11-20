"""Код Метрики на сайте: номер счётчика ищет код, страницу скачивает воркер."""

from collections.abc import Sequence

from adhelper.business.adapters.html_text import page_from_html
from adhelper.business.application.tag import (
    TagStatus,
    check_tag,
    find_city_counters,
    find_counters,
    find_default_city_id,
)
from adhelper.business.domain.ports import FetchedPage, FetchProblem, SiteUnavailable

SNIPPET = """<script type="text/javascript">
   (function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
   })(window, document, "script", "https://mc.yandex.ru/metrika/tag.js", "ym");
   ym(98765432, "init", { clickmap:true, trackLinks:true, accurateTrackBounce:true });
</script>
<noscript><div><img src="https://mc.yandex.ru/watch/98765432" alt="" /></div></noscript>"""


def test_standard_snippet_gives_one_counter() -> None:
    assert find_counters(SNIPPET) == (98765432,)


def test_noscript_and_old_counter_are_found() -> None:
    assert find_counters('<img src="https://mc.yandex.ru/watch/1234567?ut=noindex">') == (1234567,)
    old = "try { window.yaCounter7654321 = new Ya.Metrika({id:7654321}); } catch(e) {}"
    assert find_counters(old) == (7654321,)
    tag_js = '<script async src="https://mc.yandex.ru/metrika/tag.js?id=54127669"></script>'
    assert find_counters(tag_js) == (54127669,)
    nuxt_cfg = "<script>window.__NUXT__={config:{public:{yandexMetric:95375303}}}</script>"
    assert find_counters(nuxt_cfg) == (95375303,)
    both = SNIPPET + "<script>ym(11111111,'init',{})</script>"
    assert find_counters(both) == (98765432, 11111111)


def test_city_counters_from_nuxt_config() -> None:
    """Извлекает региональные счётчики из yandexCityMetric и город сайта по умолчанию."""
    html = """
    <script>
    window.__NUXT__ = {
        config: {
            public: {
                yandexMetric: 95375303,
                yandexCityMetric: {"1":109077778,"3":109077486,"7":109077599},
                defaultCityId: "1"
            }
        }
    };
    </script>
    """
    city_counters = find_city_counters(html)
    assert city_counters == {"1": 109077778, "3": 109077486, "7": 109077599}
    assert find_default_city_id(html) == "1"
    assert city_counters[find_default_city_id(html) or ""] == 109077778


def test_city_counters_ignore_broken_config() -> None:
    assert find_city_counters("yandexCityMetric:") == {}
    assert find_city_counters('yandexCityMetric:{"1":12}') == {}
    assert find_city_counters("yandexCityMetric:{1:109077778}") == {"1": 109077778}
    assert find_default_city_id("<p>Без города</p>") is None


def test_city_counters_take_first_working_block_and_only_numbers() -> None:
    broken = 'yandexCityMetric:{"1":1}'
    working = 'yandexCityMetric:{"1":109077778,"3":109077486}'
    assert find_city_counters(f"{broken} ... {working}") == {"1": 109077778, "3": 109077486}
    # Ключом служит номер города: город по умолчанию ищется по тому же правилу.
    assert find_city_counters('yandexCityMetric:{"ekb":109077778}') == {}
    assert find_default_city_id("<script>window.__NUXT__={defaultCityId:1}</script>") == "1"


def test_city_counters_capped_by_max_counters() -> None:
    many = ",".join(f'"{number}":{10000000 + number}' for number in range(1, 26))
    assert len(find_city_counters(f"yandexCityMetric:{{{many}}}")) == 20


def test_city_counters_do_not_become_the_main_counter() -> None:
    """Счётчики городов не попадают в основной список: иначе кампания получила бы чужой город."""
    html = (
        "<script>window.__NUXT__={config:{public:{yandexMetric:95375303,"
        'yandexCityMetric:{"1":109077778,"3":109077486},defaultCityId:"1"}}}</script>'
    )
    assert find_counters(html) == (95375303,)


def test_other_numbers_are_not_counters() -> None:
    page = "<p>Звоните 89161234567, маникюр 1500 ₽</p><script>gtag('config','G-1234567')</script>"
    assert find_counters(page) == ()
    assert find_counters("ym(12, 'init')") == ()


def test_page_keeps_counters_but_text_has_no_script() -> None:
    page = page_from_html("https://nogti.ru/", f"<html><body><p>Маникюр</p>{SNIPPET}</body></html>")
    assert page.counters == (98765432,)
    assert "ym(" not in page.text


class Pages:
    def __init__(
        self,
        counters: Sequence[int] | None,
        city_counters: dict[str, int] | None = None,
        default_city_id: str | None = None,
    ) -> None:
        self.counters = counters
        self.city_counters = city_counters or {}
        self.default_city_id = default_city_id

    async def fetch(self, url: str) -> FetchedPage:
        if self.counters is None:
            raise SiteUnavailable(FetchProblem.UNREACHABLE)
        return FetchedPage(
            url,
            "Маникюр",
            (),
            counters=tuple(self.counters),
            city_counters=self.city_counters,
            default_city_id=self.default_city_id,
        )


async def test_check_reports_found_missing_and_failed() -> None:
    found = await check_tag("https://nogti.ru/", Pages([98765432]))
    assert (found.status, found.counters, found.problem) == (TagStatus.FOUND, (98765432,), None)
    missing = await check_tag("https://nogti.ru/", Pages([]))
    assert (missing.status, missing.counters) == (TagStatus.MISSING, ())
    failed = await check_tag("https://nogti.ru/", Pages(None))
    assert (failed.status, failed.problem) == (TagStatus.FAILED, "unreachable")
