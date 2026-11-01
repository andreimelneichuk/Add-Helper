"""Судья проверяет смысл и может только снять объявление. Модель поддельная."""

import json

import pytest

from adhelper.creatives.adapters.rules_file import load_rules
from adhelper.creatives.application.generate import generate_ads
from adhelper.creatives.domain.judge import (
    JUDGE_SYSTEM,
    build_judge_user,
    read_verdict,
    segments,
)
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    CreativeSource,
    ModelReply,
    ModelUnavailable,
    Service,
    ViolationKind,
)

RULES = load_rules("creatives-1")
PROFILE = BusinessProfile("Лак", (Service("Маникюр", 1500),), "ул. Ленина, 5")
GOOD = {"headline": "Маникюр за 1500 ₽", "text": "Запишитесь онлайн."}


def ads(*items: dict[str, str]) -> str:
    return json.dumps({"ads": list(items)}, ensure_ascii=False)


def verdict(*statuses: str) -> str:
    items = [{"index": i, "status": s} for i, s in enumerate(statuses, 1)]
    return json.dumps({"segments": items}, ensure_ascii=False)


AD = AdDraft("Маникюр за 1500 ₽", "Стерильно. Ждём вас!")


class FakeModel:
    def __init__(self, *replies: str | Exception, tokens: int = 100) -> None:
        self.replies = list(replies)
        self.tokens = tokens
        self.calls: list[tuple[str, str]] = []

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply:
        self.calls.append((system, user))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ModelReply(reply, self.tokens)


# --- Решает код, не модель ---


def test_code_splits_ad_into_fragments() -> None:
    assert segments(AD) == ["Маникюр за 1500 ₽", "Стерильно.", "Ждём вас!"]
    # Сокращение в адресе не рвёт предложение.
    assert segments(AdDraft("Маникюр", "Ждём: ул. Ленина, 5.")) == [
        "Маникюр",
        "Ждём: ул. Ленина, 5.",
    ]


def test_unconfirmed_fragment_is_blocked() -> None:
    result = read_verdict(verdict("confirmed", "unconfirmed", "not_a_claim"), AD)
    assert result.blocked
    assert result.reason == "Стерильно."


def test_all_confirmed_or_calls_pass() -> None:
    assert not read_verdict(verdict("confirmed", "confirmed", "not_a_claim"), AD).blocked


def test_fragment_without_status_is_blocked() -> None:
    # Модель могла молча пропустить фрагмент с выдумкой: без оценки он не проходит.
    assert read_verdict(verdict("confirmed", "confirmed"), AD).blocked


def test_models_own_verdict_is_ignored() -> None:
    reply = json.dumps(
        {"verdict": "pass", "segments": [{"index": i, "status": "unconfirmed"} for i in (1, 2, 3)]}
    )
    assert read_verdict(reply, AD).blocked


@pytest.mark.parametrize(
    "bad",
    [
        "не json",
        "{}",
        '{"segments": "нет"}',
        '{"segments":[{"x":1}]}',
        '{"segments":[{"index":"a"}]}',
    ],
)
def test_unreadable_reply_fails_closed(bad: str) -> None:
    assert read_verdict(bad, AD).blocked


def test_judge_sees_profile_and_ad_as_data() -> None:
    user = build_judge_user(AdDraft("Маникюр", "Скидка 90%"), PROFILE)
    assert "ПРОФИЛЬ" in user and "ФРАГМЕНТЫ" in user and "2. Скидка 90%" in user
    assert "данные" in JUDGE_SYSTEM


# --- Судья в сценарии генерации ---


async def test_judge_removes_draft_that_passed_code_checks() -> None:
    model = FakeModel(ads(GOOD), ads(GOOD))
    judge = FakeModel(verdict("confirmed", "unconfirmed"), verdict("confirmed", "unconfirmed"))
    result = await generate_ads(PROFILE, model, RULES, judge)
    # Код пропустил, судья снял все — остаётся шаблон.
    assert result.source is CreativeSource.TEMPLATE
    kinds = {v.kind for r in result.rejected for v in r.violations}
    assert ViolationKind.UNCONFIRMED_BY_JUDGE in kinds
    assert "judge_rejected_all" in result.notes


async def test_judge_keeps_confirmed_draft() -> None:
    model = FakeModel(ads(GOOD))
    judge = FakeModel(verdict("confirmed", "not_a_claim"))
    result = await generate_ads(PROFILE, model, RULES, judge)
    assert result.source is CreativeSource.MODEL
    assert [d.headline for d in result.drafts] == [GOOD["headline"]]
    assert result.tokens_used == 200  # вызов модели плюс вызов судьи


async def test_judge_failure_fails_closed() -> None:
    model = FakeModel(ads(GOOD), ads(GOOD))
    judge = FakeModel(ModelUnavailable("x"), ModelUnavailable("x"))
    result = await generate_ads(PROFILE, model, RULES, judge)
    assert result.source is CreativeSource.TEMPLATE


async def test_judge_never_sees_drafts_the_code_rejected() -> None:
    invented = {"headline": "Маникюр за 500 ₽", "text": "Только сегодня!"}
    model = FakeModel(ads(invented), ads(invented))
    judge = FakeModel(verdict("confirmed"), verdict("confirmed"))
    result = await generate_ads(PROFILE, model, RULES, judge)
    # Код снял объявление за выдуманную цену; судья его не одобряет, потому что не вызывается.
    assert result.source is CreativeSource.TEMPLATE
    assert judge.calls == []


async def test_without_judge_behaves_as_before() -> None:
    result = await generate_ads(PROFILE, FakeModel(ads(GOOD)), RULES)
    assert result.source is CreativeSource.MODEL
    assert result.tokens_used == 100
