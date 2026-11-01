from pydantic import SecretStr

from adhelper.creatives.adapters.openai_compatible import OpenAiCompatibleModel
from adhelper.creatives.domain.model import TextModel


def build_text_model(
    base_url: str, api_key: SecretStr | None, model: str, project: str = ""
) -> TextModel | None:
    """Провайдер задаётся адресом OpenAI-совместимого API. Без ключа или модели — шаблоны."""
    if not base_url.startswith(("https://", "http://")):
        raise ValueError(f"Адрес модели должен начинаться с https://: {base_url!r}")
    if api_key is None or not api_key.get_secret_value() or not model:
        return None
    return OpenAiCompatibleModel(base_url, api_key, model, project)
