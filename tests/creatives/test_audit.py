"""Аудитор только сообщает о нарушениях, разбор ответа делает код (docs/12-agents.md §4)."""

import json

from adhelper.creatives.domain.audit import build_audit_user, read_audit
from adhelper.creatives.domain.model import AdDraft, BusinessProfile, Service

PROFILE = BusinessProfile("Лак", (Service("Маникюр", 1500),), "ул. Ленина, 5")
DRAFTS = [AdDraft("Маникюр за 1500 ₽", "Ждём вас."), AdDraft("Лучший маникюр", "Ждём вас.")]


def test_ads_are_numbered_as_data() -> None:
    user = build_audit_user(DRAFTS, PROFILE)
    assert "ПРОФИЛЬ:" in user and "ОБЪЯВЛЕНИЯ:" in user
    assert '"n": 2' in user and "Лучший маникюр" in user


def test_violations_block_only_their_ad() -> None:
    reply = json.dumps({"ads": [{"n": 1, "violations": []}, {"n": 2, "violations": ["Лучший"]}]})
    assert [v.blocked for v in read_audit(reply, DRAFTS)] == [False, True]


def test_missing_answer_and_bad_reply_block() -> None:
    reply = json.dumps({"ads": [{"n": 1, "violations": []}]})
    assert [v.reason for v in read_audit(reply, DRAFTS)] == ["", "no_audit_answer"]
    assert all(v.blocked for v in read_audit("не json", DRAFTS))
