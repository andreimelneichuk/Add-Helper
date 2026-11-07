import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adhelper.accounts.adapters.crypto import AesGcmCipher
from adhelper.accounts.adapters.sql import SqlAccountTokens
from adhelper.accounts.adapters.unconfigured import UnconfiguredProvider
from adhelper.accounts.application.service import token_context
from adhelper.accounts.domain.model import OAuthTokens
from adhelper.config import Settings
from adhelper.platforms.adapters.direct.factory import DirectPlatforms, build_direct_platforms
from adhelper.platforms.adapters.direct.transport import DirectAuthError, DirectHttpClient
from adhelper.worker.runtime import open_runtime
from tests.platforms.direct.fake_direct import FakeDirect
from tests.seed import seed_account

Sessions = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
LOGIN = "salon-romashka"


def _cipher() -> AesGcmCipher:
    return AesGcmCipher([("k1", b"k" * 32)])


async def _connect(sessions: Sessions, business_id: uuid.UUID, access: str) -> None:
    cipher = _cipher()
    await SqlAccountTokens(sessions, business_id).save_connected(
        LOGIN,
        "yandex_direct",
        cipher.encrypt(access, token_context(business_id, LOGIN, "access")),
        cipher.encrypt("refresh", token_context(business_id, LOGIN, "refresh")),
        OAuthTokens(access, "refresh", NOW + timedelta(days=300), ("direct:api",)),
    )


def test_platform_is_off_until_enabled(settings: Settings) -> None:
    sessions: Sessions = async_sessionmaker()
    assert (
        build_direct_platforms(settings, sessions, UnconfiguredProvider(), _cipher(), lambda: NOW)
        is None
    )
    enabled = settings.model_copy(update={"direct_enabled": True})
    assert build_direct_platforms(enabled, sessions, None, _cipher(), lambda: NOW) is None
    assert (
        build_direct_platforms(enabled, sessions, UnconfiguredProvider(), None, lambda: NOW) is None
    )


async def test_token_comes_from_own_business_only(sessions: Sessions) -> None:
    mine, other = uuid.uuid4(), uuid.uuid4()
    await seed_account(sessions, mine)
    await seed_account(sessions, other)
    await _connect(sessions, mine, "secret-access")
    direct = FakeDirect()
    client = DirectHttpClient(transport=direct.transport())
    platforms = DirectPlatforms(client, sessions, UnconfiguredProvider(), _cipher(), lambda: NOW)

    assert await platforms(mine, LOGIN).read_campaigns(LOGIN) == []
    assert direct.requests[0]["headers"]["authorization"] == "Bearer secret-access"
    # Тот же логин у другого бизнеса: токена нет, нужен вход владельца, запроса нет.
    with pytest.raises(DirectAuthError):
        await platforms(other, LOGIN).read_campaigns(LOGIN)
    assert len(direct.requests) == 1
    await platforms.aclose()


async def test_disabled_runtime_has_no_platform(settings: Settings) -> None:
    async with open_runtime(settings) as runtime:
        assert runtime.platform_for(uuid.uuid4(), LOGIN) is None


async def test_token_written_with_another_key_asks_owner_to_reconnect(sessions: Sessions) -> None:
    business_id = uuid.uuid4()
    await seed_account(sessions, business_id)
    await _connect(sessions, business_id, "secret-access")
    other_key = AesGcmCipher([("k2", b"x" * 32)])
    direct = FakeDirect()
    client = DirectHttpClient(transport=direct.transport())
    platforms = DirectPlatforms(client, sessions, UnconfiguredProvider(), other_key, lambda: NOW)

    with pytest.raises(DirectAuthError):
        await platforms(business_id, LOGIN).read_campaigns(LOGIN)
    assert direct.requests == []
    await platforms.aclose()
