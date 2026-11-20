import pytest

from adhelper.campaigns.application.launch import expand_keywords_with_model
from adhelper.campaigns.domain.keywords import (
    AUTOTARGETING_KEYWORD,
    STOP_WORDS,
    generate_keywords,
    negative_keywords,
    parse_synonyms_response,
)
from adhelper.creatives.domain.model import ModelReply, ModelUnavailable, Service


class FakeTextModel:
    def __init__(self, reply: ModelReply | Exception) -> None:
        self.reply = reply
        self.called_with: list[tuple[str, str]] = []

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        self.called_with.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


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


def test_parse_synonyms_response_valid() -> None:
    reply = """{
        "synonyms": [
            {
                "service": "Покрытие гель-лак",
                "terms": ["шеллак", "ногти гель лак", "маникюр с покрытием"],
            },
            {"service": "Чистка лица", "terms": ["пилинг лица", "ультразвуковая чистка"]}
        ]
    }"""
    synonyms = parse_synonyms_response(reply)
    assert "шеллак" in synonyms
    assert "ногти гель лак" in synonyms
    assert "маникюр с покрытием" in synonyms
    assert "пилинг лица" in synonyms


def test_parse_synonyms_response_filters_stop_words_and_formatting() -> None:
    reply = """{
        "synonyms": [
            {
                "service": "Маникюр",
                "terms": [
                    "маникюр бесплатно",
                    "скачать фото ногти",
                    "курсы маникюра",
                    "1500 руб шеллак",
                    "ногти"
                ]
            }
        ]
    }"""
    synonyms = parse_synonyms_response(reply)
    assert "ногти" in synonyms
    assert "шеллак" in synonyms
    # Стоп-слова исключены
    assert not any("бесплатно" in s or "скачать" in s or "курсы" in s for s in synonyms)


def test_parse_synonyms_response_markdown_and_trailing_commas() -> None:
    reply = """```json
    {
        "synonyms": [
            {
                "service": "Укладка",
                "terms": ["прическа", "локоны",],
            },
        ],
    }
    ```"""
    synonyms = parse_synonyms_response(reply)
    assert "прическа" in synonyms
    assert "локоны" in synonyms


def test_parse_synonyms_response_invalid_json() -> None:
    assert parse_synonyms_response("") == []
    assert parse_synonyms_response("не json") == []
    assert parse_synonyms_response('{"foo": "bar"}') == []


@pytest.mark.anyio
async def test_expand_keywords_with_model_success() -> None:
    reply_json = """{
        "synonyms": [
            {"service": "Маникюр", "terms": ["шеллак", "ногти гель лак"]}
        ]
    }"""
    model = FakeTextModel(ModelReply(text=reply_json, tokens=40))
    services = [Service("Маникюр")]

    keywords = await expand_keywords_with_model(
        model, services, city="Пермь", include_autotargeting=True
    )

    assert keywords[0] == AUTOTARGETING_KEYWORD
    assert "маникюр пермь" in keywords
    assert "шеллак пермь" in keywords
    assert "ногти гель лак пермь" in keywords
    assert len(model.called_with) == 1


@pytest.mark.anyio
async def test_expand_keywords_with_model_none_fallback() -> None:
    services = [Service("Педикюр")]
    keywords = await expand_keywords_with_model(None, services, city="Москва")

    assert keywords[0] == AUTOTARGETING_KEYWORD
    assert "педикюр москва" in keywords


@pytest.mark.anyio
async def test_expand_keywords_with_model_failure_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = FakeTextModel(ModelUnavailable("length", tokens=300))
    services = [Service("Стрижка волос")]

    # При сбое модели метод не падает, а безопасно возвращает базовые фразы
    keywords = await expand_keywords_with_model(model, services, city="Казань")

    assert keywords[0] == AUTOTARGETING_KEYWORD
    assert "стрижка волос казань" in keywords
    assert "обрезано лимитом модели" in caplog.text


def test_delivery_keywords_use_delivery_words_not_salon() -> None:
    """Доставка еды: «доставка» и «заказать», а не «салон» и «записаться»."""
    services = [Service("Чеддер на Урале 964 г / 32 шт", 699)]
    keywords = generate_keywords(services, niche="доставка еды", city="Екатеринбург")

    assert "доставка чеддер на урале екатеринбург" in keywords
    assert "заказать чеддер на урале екатеринбург" in keywords
    assert not any("салон" in k or "запис" in k for k in keywords)


def test_weight_and_pieces_do_not_become_part_of_the_phrase() -> None:
    services = [Service("Зачетный сет ±1208 г / 48 шт", 869)]
    keywords = generate_keywords(services, niche="доставка еды", city="Екатеринбург")

    assert "зачетный сет екатеринбург" in keywords
    assert not any(" сет г" in k or " шт" in k for k in keywords)


def test_salon_keywords_keep_salon_words() -> None:
    services = [Service("Маникюр")]
    keywords = generate_keywords(services, niche="салон красоты", city="Пермь")

    assert "маникюр салон пермь" in keywords
    assert "записаться на маникюр пермь" in keywords


def test_niche_is_matched_by_whole_word() -> None:
    """«Победа» не содержит «еда»: ниша ищется словом, а не подстрокой."""
    keywords = generate_keywords([Service("Ремонт")], niche="Победа", city="Москва")

    assert not any("доставка" in k for k in keywords)


def test_unknown_niche_does_not_add_salon_words() -> None:
    """Неизвестная ниша — только общие слова: чужие «салон» и «запись» хуже молчания."""
    keywords = generate_keywords([Service("Ремонт холодильников")], city="Тюмень")

    assert not any("салон" in k or "запис" in k for k in keywords)


def test_delivery_negative_keywords_filter_recipes_and_jobs() -> None:
    negatives = negative_keywords("доставка еды")

    assert "рецепт" in negatives
    assert "как приготовить" in negatives
    assert "вакансии" in negatives
    assert "франшиза" in negatives


def test_unknown_niche_has_basic_negative_keywords() -> None:
    negatives = negative_keywords("")

    assert "бесплатно" in negatives
    assert "вакансии" in negatives
