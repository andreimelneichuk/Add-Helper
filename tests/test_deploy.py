"""Настройки развёртывания, от которых зависит безопасность сервиса."""

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_app_sees_real_client_address_behind_caddy() -> None:
    # Лимит разборов до входа считается по адресу человека, а не по адресу Caddy.
    compose = (ROOT / "compose.yaml").read_text()
    assert "--proxy-headers" in compose
    assert "--forwarded-allow-ips=*" in compose
    # Caddy заменяет чужой X-Forwarded-For своим, пока ему не велели доверять клиенту.
    assert "trusted_proxies" not in (ROOT / "deploy" / "Caddyfile").read_text()
    # Приложение открыто только Caddy, а не всей сети: иначе заголовок мог бы подставить кто угодно.
    assert '"127.0.0.1:${APP_PORT:-8000}:8000"' in compose


# Задаются самим compose или нужны только стенду сквозных тестов.
NOT_FROM_ENV_FILE = {
    "postgres_host",
    "postgres_port",
    "postgres_user",
    "postgres_db",
    "cookie_secure",
    "yandex_oauth_url",
    "yandex_login_url",
}


def test_every_setting_reaches_app_from_env_file() -> None:
    # Заказчик подключает свои приложения через .env: переменная, которую compose не передаёт,
    # молча остаётся пустой, и вход отвечает «не настроено».
    from adhelper.config import Settings

    compose = (ROOT / "compose.yaml").read_text()
    example = (ROOT / ".env.example").read_text()
    for field, info in Settings.model_fields.items():
        if field in NOT_FROM_ENV_FILE:
            continue
        name = (info.alias or field).upper()
        assert f"{name}: ${{{name}" in compose, name
        assert f"\n{name}=" in example, name
