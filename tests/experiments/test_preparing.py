"""Тест готовится без варианта: владелец может отменить, показы не тронуты. docs/04 §5."""

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from adhelper.experiments.application.service import (
    Hypothesis,
    ModerationState,
    TestAlreadyRunning,
)
from adhelper.experiments.domain.model import IllegalTransition, Status
from adhelper.ledger.adapters.sql import SqlChangeRepository
from tests.experiments.test_scenarios import (
    BUSINESS,
    Clock,
    Sessions,
    _owners,  # noqa: F401  — тот же бизнес и кабинет в базе
    build,
    fake,
    running,
)


async def test_preparing_test_holds_campaign_and_touches_nothing(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = fake()
    service = build(platform, sessions, engine, Clock())

    test_id = await service.prepare("acc", "c1", "ad:a", Hypothesis.PRICE)

    record = await service.get(test_id)
    assert record.status is Status.REVIEW
    assert record.variant_ref is None
    with pytest.raises(TestAlreadyRunning):
        await service.prepare("acc", "c1", "ad:x", Hypothesis.BENEFIT)
    assert platform.ads == {"ad:a": True, "ad:b": False}
    assert await SqlChangeRepository(sessions, BUSINESS).all() == []


async def test_test_without_variant_cannot_start(sessions: Sessions, engine: AsyncEngine) -> None:
    service = build(fake(), sessions, engine, Clock())
    test_id = await service.prepare("acc", "c1", "ad:a", Hypothesis.PRICE)

    with pytest.raises(ValueError, match="варианта"):
        await service.moderation(test_id, ModerationState.APPROVED)
    assert (await service.get(test_id)).status is Status.REVIEW


async def test_owner_cancels_preparing_test_and_campaign_is_free(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    service = build(fake(), sessions, engine, Clock())
    test_id = await service.prepare("acc", "c1", "ad:a", Hypothesis.PRICE)

    await service.cancel(test_id)

    record = await service.get(test_id)
    assert record.status is Status.CANCELLED
    assert record.reason == "owner_cancelled"
    assert await service.prepare("acc", "c1", "ad:a", Hypothesis.BENEFIT) > test_id


async def test_running_test_is_not_cancelled_by_this_path(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    # Идущий тест уже переключал объявления: его отмена — через журнал, это не сделано.
    clock = Clock()
    service = build(fake(), sessions, engine, clock)
    test_id = await running(service)
    clock.now += timedelta(days=1)

    with pytest.raises(IllegalTransition):
        await service.cancel(test_id)
    assert (await service.get(test_id)).status is Status.RUNNING
