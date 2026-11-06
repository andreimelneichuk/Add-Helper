from adhelper.business.adapters.site_images import image_urls

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
