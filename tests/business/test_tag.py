"""Код Метрики на сайте: номер счётчика ищет код, страницу скачивает воркер."""

from collections.abc import Sequence

from adhelper.business.adapters.html_text import page_from_html
from adhelper.business.application.tag import TagStatus, check_tag, find_counters
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
    both = SNIPPET + "<script>ym(11111111,'init',{})</script>"
    assert find_counters(both) == (98765432, 11111111)


def test_other_numbers_are_not_counters() -> None:
    page = "<p>Звоните 89161234567, маникюр 1500 ₽</p><script>gtag('config','G-1234567')</script>"
    assert find_counters(page) == ()
    assert find_counters("ym(12, 'init')") == ()


def test_page_keeps_counters_but_text_has_no_script() -> None:
    page = page_from_html("https://nogti.ru/", f"<html><body><p>Маникюр</p>{SNIPPET}</body></html>")
    assert page.counters == (98765432,)
    assert "ym(" not in page.text


class Pages:
    def __init__(self, counters: Sequence[int] | None) -> None:
        self.counters = counters

    async def fetch(self, url: str) -> FetchedPage:
        if self.counters is None:
            raise SiteUnavailable(FetchProblem.UNREACHABLE)
        return FetchedPage(url, "Маникюр", (), tuple(self.counters))


async def test_check_reports_found_missing_and_failed() -> None:
    found = await check_tag("https://nogti.ru/", Pages([98765432]))
    assert (found.status, found.counters, found.problem) == (TagStatus.FOUND, (98765432,), None)
    missing = await check_tag("https://nogti.ru/", Pages([]))
    assert (missing.status, missing.counters) == (TagStatus.MISSING, ())
    failed = await check_tag("https://nogti.ru/", Pages(None))
    assert (failed.status, failed.problem) == (TagStatus.FAILED, "unreachable")
