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

    # Без ключа объявления собираются из шаблонов профиля.
    text_model_provider: str = "yandex"
    text_model_api_key: SecretStr | None = None
    text_model_folder: str = ""
    text_model_name: str = "yandexgpt-5.1"

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
