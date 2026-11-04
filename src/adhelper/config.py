from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_user: str = "adhelper"
    postgres_password: SecretStr
    postgres_db: str = "adhelper"

    public_base_url: str = "http://localhost:8000"
    cookie_secure: bool = True
    # Пусто: вход отключён. Токены без шифрования хранить нельзя.
    token_encryption_keys: SecretStr | None = None

    # Приложение Яндекс ID заказчика. Пусто: вход и подключение кабинета отвечают 503.
    yandex_client_id: str = ""
    yandex_client_secret: SecretStr | None = None
    # Адреса Яндекс ID меняются только для стенда сквозных тестов.
    yandex_oauth_url: str = "https://oauth.yandex.ru"
    yandex_login_url: str = "https://login.yandex.ru"

    # Любой OpenAI-совместимый API. Без ключа или модели объявления собираются из шаблонов.
    text_model_base_url: str = "https://ai.api.cloud.yandex.net/v1"
    text_model_api_key: SecretStr | None = None
    # Заголовок OpenAI-Project; для Yandex AI Studio это каталог.
    text_model_project: str = ""
    text_model_name: str = ""
    # Семантическая проверка объявлений тем же API. Пусто — судья выключен, остаётся код.
    judge_model_name: str = ""

    # Письма о тревогах через любой SMTP. Пустой адрес сервера: письма ждут настройки.
    smtp_host: str = ""
    # 465 — сразу TLS; 587 — с SMTP_STARTTLS=true.
    smtp_port: int = 465
    smtp_starttls: bool = False
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = ""
    # Бот Telegram от @BotFather. Без токена владелец не может привязать Telegram.
    telegram_bot_token: SecretStr | None = None
    telegram_bot_name: str = ""

    @property
    def postgres_dsn(self) -> str:
        url = self.database_url.set(drivername="postgresql")
        return url.render_as_string(hide_password=False)

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
