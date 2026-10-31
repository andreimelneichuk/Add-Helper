from pydantic import SecretStr

from adhelper.creatives.adapters.yandex import YandexAiStudioModel
from adhelper.creatives.domain.model import TextModel


def build_text_model(
    provider: str, api_key: SecretStr | None, folder: str, model: str
) -> TextModel | None:
    """Провайдер задаётся конфигурацией. Без ключа работаем на шаблонах из профиля."""
    if provider == "none":
        return None
    if provider != "yandex":
        raise ValueError(f"Неизвестный провайдер модели: {provider}")
    if api_key is None or not api_key.get_secret_value() or not folder:
        return None
    return YandexAiStudioModel(api_key, folder, model)
