from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from pydantic import SecretStr

from adhelper.accounts.adapters.yandex_oauth import SplitYandexOAuth, YandexOAuth
from adhelper.config import Settings
from adhelper.platforms.adapters.direct.factory import yandex_provider


def _clock() -> datetime:
    return datetime(2026, 11, 7, tzinfo=UTC)


def _split() -> SplitYandexOAuth:
    return SplitYandexOAuth(
        YandexOAuth("login-app", SecretStr("s1"), _clock),
        YandexOAuth("direct-app", SecretStr("s2"), _clock),
    )


def _client(url: str) -> str:
    return parse_qs(urlsplit(url).query)["client_id"][0]


def test_login_goes_to_login_app_and_cabinet_to_direct_app() -> None:
    provider = _split()
    login = provider.authorize_url("s", "c", "https://x/api/auth/yandex/callback", ["login:info"])
    cabinet = provider.authorize_url(
        "s", "c", "https://x/api/accounts/yandex/callback", ["direct:api", "login:info"]
    )
    assert _client(login) == "login-app"
    assert _client(cabinet) == "direct-app"


def test_second_app_is_used_only_when_both_settings_are_given() -> None:
    base = {"yandex_client_id": "login-app", "yandex_client_secret": "s1"}
    assert isinstance(yandex_provider(Settings(**base), _clock), YandexOAuth)  # type: ignore[arg-type]
    both = Settings(**base, yandex_direct_client_id="direct-app", yandex_direct_client_secret="s2")  # type: ignore[arg-type]
    assert isinstance(yandex_provider(both, _clock), SplitYandexOAuth)
