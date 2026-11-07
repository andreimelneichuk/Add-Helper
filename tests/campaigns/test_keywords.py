from adhelper.campaigns.domain.keywords import (
    AUTOTARGETING_KEYWORD,
    STOP_WORDS,
    generate_keywords,
)
from adhelper.creatives.domain.model import Service


def test_generate_keywords_with_services_and_city() -> None:
    services = [
        Service("Маникюр с покрытием", 1500),
        Service("Педикюр аппаратный", 2200),
    ]
    keywords = generate_keywords(services, city="Пермь", include_autotargeting=True)

    assert keywords[0] == AUTOTARGETING_KEYWORD
    assert "маникюр с покрытием пермь" in keywords
    assert "маникюр с покрытием цена пермь" in keywords
    assert "педикюр аппаратный пермь" in keywords
    # Нет цен и валюты в ключевых фразах
    assert not any("1500" in k or "2200" in k or "₽" in k for k in keywords)


def test_generate_keywords_filters_stop_words() -> None:
    services = [
        Service("Маникюр бесплатно своими руками", 0),
        Service("Курсы педикюра вакансии", 0),
    ]
    keywords = generate_keywords(services, city="Москва")
    for k in keywords:
        for stop in STOP_WORDS:
            assert stop not in k.split()


def test_generate_keywords_extracts_city_from_address() -> None:
    services = [Service("Стрижка волос")]
    keywords = generate_keywords(services, address="г. Пермь, ул. Ленина, 25")
    assert any("пермь" in k for k in keywords)


def test_generate_keywords_without_services_uses_niche() -> None:
    keywords = generate_keywords([], niche="Салон красоты", city="Казань")
    assert keywords[0] == AUTOTARGETING_KEYWORD
    assert "салон красоты казань" in keywords


def test_generate_keywords_without_autotargeting() -> None:
    keywords = generate_keywords([Service("Чистка лица")], city="Уфа", include_autotargeting=False)
    assert AUTOTARGETING_KEYWORD not in keywords
    assert "чистка лица уфа" in keywords
