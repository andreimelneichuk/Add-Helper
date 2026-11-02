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
