import base64
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlencode

from adhelper.accounts.domain.model import ExternalIdentity, OAuthTokens
from adhelper.accounts.domain.ports import GrantRejected, ProviderUnavailable


@dataclass
class FakeProvider:
    """Поддельный провайдер строг: проверяет PKCE, redirect_uri и одноразовость кода."""

    now: datetime
    grants: dict[str, tuple[str, str, ExternalIdentity]] = field(default_factory=dict)
    identities: dict[str, ExternalIdentity] = field(default_factory=dict)
    refresh_tokens: dict[str, ExternalIdentity] = field(default_factory=dict)
    unavailable: bool = False
    issued: list[str] = field(default_factory=list)
    refresh_calls: int = 0
    _counter: int = 0

    def authorize_url(
        self, state: str, code_challenge: str, redirect_uri: str, scopes: Sequence[str]
    ) -> str:
        query = urlencode(
            {
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "redirect_uri": redirect_uri,
                "scope": " ".join(scopes),
            }
        )
        return f"https://oauth.test/authorize?{query}"

    def approve(self, challenge: str, redirect_uri: str, who: ExternalIdentity) -> str:
        self._counter += 1
        code = f"code-{self._counter}"
        self.grants[code] = (challenge, redirect_uri, who)
        return code

    def _issue(self, who: ExternalIdentity, ttl: timedelta) -> OAuthTokens:
        self._counter += 1
        access = f"SECRET-ACCESS-{self._counter}"
        refresh = f"SECRET-REFRESH-{self._counter}"
        self.identities[access] = who
        self.refresh_tokens[refresh] = who
        self.issued += [access, refresh]
        return OAuthTokens(access, refresh, self.now + ttl, ("direct:api",))

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> OAuthTokens:
        if self.unavailable:
            raise ProviderUnavailable
        grant = self.grants.pop(code, None)
        if grant is None:
            raise GrantRejected
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if grant[0] != challenge or grant[1] != redirect_uri:
            raise GrantRejected
        return self._issue(grant[2], timedelta(days=30))

    async def refresh(self, refresh_token: str) -> OAuthTokens:
        self.refresh_calls += 1
        if self.unavailable:
            raise ProviderUnavailable
        who = self.refresh_tokens.pop(refresh_token, None)
        if who is None:
            raise GrantRejected
        return self._issue(who, timedelta(days=30))

    async def identity(self, access_token: str) -> ExternalIdentity:
        if self.unavailable:
            raise ProviderUnavailable
        return self.identities[access_token]
