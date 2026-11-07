from typing import Any, cast

from adhelper.business.adapters.rules_file import load_site_rules
from adhelper.business.adapters.site_images import image_urls, site_image_urls
from adhelper.business.domain.ports import FetchedPage, FetchProblem, SiteUnavailable

HTML = """
<head><meta property="og:image" content="/cover.jpg"></head>
<body>
<img src="/img/work1.jpg"><img src="img/work1.jpg"><img src="//cdn.example.com/a.png">
<img src="/logo.png"><img src="/pics/icon-phone.png"><img src="/x.svg">
<img src="data:image/gif;base64,AAAA"><img data-src="/lazy.webp">
<img srcset="/set-1x.jpg 1x, /set-2x.jpg 2x">
<img src="javascript:alert(1)">
</body>
"""


def test_picks_page_images_in_order_without_noise() -> None:
    assert image_urls(HTML, "https://salon.ru/page/") == [
        "https://salon.ru/cover.jpg",
        "https://salon.ru/img/work1.jpg",
        "https://salon.ru/page/img/work1.jpg",
        "https://cdn.example.com/a.png",
        "https://salon.ru/lazy.webp",
        "https://salon.ru/set-1x.jpg",
    ]


def test_caps_candidates() -> None:
    html = "".join(f'<img src="/p{i}.jpg">' for i in range(40))
    assert len(image_urls(html, "https://salon.ru/")) == 12


def test_picks_css_backgrounds() -> None:
    html = '<div style="background-image:url(\'/bg/a.jpg\')"></div><div data-bg="/bg/b.jpg"></div>'
    assert image_urls(html, "https://salon.ru/") == [
        "https://salon.ru/bg/a.jpg",
        "https://salon.ru/bg/b.jpg",
    ]


class _Fetcher:
    def __init__(self, html: str | None) -> None:
        self.html = html

    async def get_bytes(
        self, url: str, accept: str, mimes: tuple[str, ...], max_bytes: int
    ) -> tuple[str, str, bytes]:
        if self.html is None:
            raise SiteUnavailable(FetchProblem.UNREACHABLE)
        return url, "text/html", self.html.encode()


class _Renderer:
    async def render(self, url: str) -> FetchedPage:
        return FetchedPage(url, "", images=("https://salon.ru/js1.jpg", "https://salon.ru/js2.jpg"))


async def test_uses_browser_when_markup_has_few_images() -> None:
    rules = load_site_rules()
    found = await site_image_urls(
        cast(Any, _Fetcher("<p>shell</p>")), "https://salon.ru/", rules, _Renderer()
    )
    assert found == ["https://salon.ru/js1.jpg", "https://salon.ru/js2.jpg"]


async def test_skips_browser_when_markup_is_enough() -> None:
    html = "".join(f'<img src="/p{i}.jpg">' for i in range(3))
    found = await site_image_urls(
        cast(Any, _Fetcher(html)), "https://salon.ru/", load_site_rules(), _Renderer()
    )
    assert found is not None and len(found) == 3 and "js1" not in found[0]


async def test_unreachable_site_without_browser_result_is_none() -> None:
    class _Broken:
        async def render(self, url: str) -> FetchedPage:
            raise SiteUnavailable(FetchProblem.UNREACHABLE)

    assert (
        await site_image_urls(
            cast(Any, _Fetcher(None)), "https://salon.ru/", load_site_rules(), _Broken()
        )
        is None
    )
