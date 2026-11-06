from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "env", "/etc/secrets/.env", "/etc/secrets/env"),
        extra="ignore",
    )

    # Neon / Lakebase Postgres
    database_url_env: SecretStr | None = Field(default=None, alias="database_url")
    database_url_unpooled_env: SecretStr | None = Field(default=None, alias="database_url_unpooled")

    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_user: str = "adhelper"
    postgres_password: SecretStr = SecretStr("")
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
    # Боевой адаптер Директа. Выключен: действия с площадкой отвечают 503, страж шлёт тревогу.
    # Включать после согласования боевого запуска с владельцем; для проверки — адрес песочницы.
    direct_enabled: bool = False
    direct_api_url: str = "https://api.direct.yandex.com"
    direct_live_url: str = "https://api.direct.yandex.ru/live/v4/json/"

    # Любой OpenAI-совместимый API. Без ключа или модели объявления собираются из шаблонов.
    # По умолчанию пара, прошедшая ворота выпуска на OpenRouter 2026-11-05 (docs/12-agents.md §4).
    # Другую пару ставь только после своих ворот: evals.gate.
    text_model_base_url: str = "https://openrouter.ai/api/v1"
    text_model_api_key: SecretStr | None = None
    # Заголовок OpenAI-Project; для Yandex AI Studio это каталог.
    text_model_project: str = ""
    text_model_name: str = "google/gemma-4-31b-it"
    # Семантическая проверка объявлений тем же API. Пусто — судья выключен, остаётся код.
    judge_model_name: str = "deepseek/deepseek-v4.1-flash"
    # Генерация картинок тем же ключом OpenRouter (один баланс), модели в правилах images-1.
    # Каждая картинка стоит денег (около $0,03), поэтому по умолчанию выключена: объявления
    # идут со своими фото владельца или без картинки.
    image_generation: bool = False

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
        if self.database_url_env:
            raw = self.database_url_env.get_secret_value()
            if raw:
                url = make_url(raw).set(drivername="postgresql")
                return url.render_as_string(hide_password=False)
        url = self.database_url.set(drivername="postgresql")
        return url.render_as_string(hide_password=False)

    @property
    def postgres_dsn_unpooled(self) -> str:
        if self.database_url_unpooled_env:
            raw = self.database_url_unpooled_env.get_secret_value()
            if raw:
                url = make_url(raw).set(drivername="postgresql")
                return url.render_as_string(hide_password=False)
        return self.postgres_dsn

    @property
    def database_url(self) -> URL:
        if self.database_url_env:
            raw = self.database_url_env.get_secret_value()
            if raw:
                return make_url(raw).set(drivername="postgresql+psycopg")
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @property
    def migration_database_url(self) -> URL:
        if self.database_url_unpooled_env:
            raw = self.database_url_unpooled_env.get_secret_value()
            if raw:
                return make_url(raw).set(drivername="postgresql+psycopg")
        return self.database_url
