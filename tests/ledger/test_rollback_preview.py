"""Предпросмотр отмены и отмена всего запроса: docs/04-autopilot.md §6, сценарий 25."""

from sqlalchemy.ext.asyncio import AsyncEngine

from adhelper.budget.domain.limits import CampaignBudget
from adhelper.ledger.adapters.sql import SqlChangeRepository
from adhelper.ledger.application.rollback import (
    RollbackStatus,
    preview_rollback,
    request_changes,
    rollback_change,
    rollback_request,
)
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus, ProposedChange
from tests.budget.fake_platform import FakePlatform
from tests.ledger.test_rollback import (
    BUSINESS,
    NOW,
    Sessions,
    _owners,  # noqa: F401  — тот же бизнес и кабинет в базе
    done,
    limit,
    make,
    pause,
    platform_with,
)


def two_campaigns() -> FakePlatform:
    platform = FakePlatform(
        [
            CampaignBudget("c1", 500_000_000, 0, True, None),
            CampaignBudget("c2", 500_000_000, 0, True, None),
        ],
        NOW,
    )
    platform.ads = {"ad:1": True, "ad:2": True}
    return platform


def user_pause(ref: str, request: str) -> ProposedChange:
    # Так владелец выключает показы по всем направлениям: один запрос, ключ на направление.
    return ProposedChange(
        "acc",
        ChangeSource.USER,
        ChangeKind.PAUSE_AD,
        ref,
        {"active": False},
        "Пауза",
        f"schedule:{request}:{ref.removeprefix('ad:')}",
    )


async def test_preview_changes_nothing(sessions: Sessions, engine: AsyncEngine) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    original = await done(ledger, pause())

    preview = await preview_rollback(ledger, original)

    assert preview.status is RollbackStatus.READY
    assert preview.change is not None and preview.change.id == original
    assert platform.ads["ad:1"] is False
    assert len(await SqlChangeRepository(sessions, BUSINESS).all()) == 1


async def test_preview_names_later_changes(sessions: Sessions, engine: AsyncEngine) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    first = await done(ledger, limit("c1", 450_000_000))
    second = await done(ledger, limit("c1", 400_000_000))

    preview = await preview_rollback(ledger, first)

    assert preview.status is RollbackStatus.HAS_LATER_CHANGES
    assert [c.id for c in preview.later] == [second]


async def test_preview_sees_change_made_in_cabinet(sessions: Sessions, engine: AsyncEngine) -> None:
    platform = platform_with()
    ledger = make(platform, sessions, engine)
    original = await done(ledger, pause())
    platform.ads["ad:1"] = True

    preview = await preview_rollback(ledger, original)

    assert preview.status is RollbackStatus.CHANGED_OUTSIDE
    assert preview.actual == {"active": True, "archived": False}


async def test_preview_of_reverted_change(sessions: Sessions, engine: AsyncEngine) -> None:
    ledger = make(platform_with(), sessions, engine)
    original = await done(ledger, pause())
    await rollback_change(ledger, original)

    assert (await preview_rollback(ledger, original)).status is RollbackStatus.ALREADY_REVERTED


async def test_request_groups_campaigns_of_one_request(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    ledger = make(two_campaigns(), sessions, engine)
    first = await done(ledger, user_pause("ad:1", "r1"))
    second = await done(ledger, user_pause("ad:2", "r1"))
    await done(ledger, user_pause("ad:1", "r2"))

    group = await request_changes(ledger, first)

    assert [c.id for c in group] == [first, second]


async def test_autopilot_changes_are_not_grouped(sessions: Sessions, engine: AsyncEngine) -> None:
    ledger = make(two_campaigns(), sessions, engine)
    first = await done(ledger, pause("ad:1"))
    await done(ledger, pause("ad:2"))

    assert [c.id for c in await request_changes(ledger, first)] == [first]


async def test_whole_request_is_reverted(sessions: Sessions, engine: AsyncEngine) -> None:
    platform = two_campaigns()
    ledger = make(platform, sessions, engine)
    first = await done(ledger, user_pause("ad:1", "r1"))
    await done(ledger, user_pause("ad:2", "r1"))

    result = await rollback_request(ledger, first)

    assert result.status is RollbackStatus.APPLIED
    assert platform.ads == {"ad:1": True, "ad:2": True}
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    reverts = [r for r in rows if r.source is ChangeSource.ROLLBACK]
    assert len(reverts) == 2
    assert all(r.status is ChangeStatus.APPLIED for r in reverts)


async def test_request_is_not_touched_when_one_part_cannot_be_undone(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    # Одно направление изменили в кабинете: отмена не трогает ни одно, владелец решает сам.
    platform = two_campaigns()
    ledger = make(platform, sessions, engine)
    first = await done(ledger, user_pause("ad:1", "r1"))
    await done(ledger, user_pause("ad:2", "r1"))
    platform.ads["ad:2"] = True

    result = await rollback_request(ledger, first)

    assert result.status is RollbackStatus.CHANGED_OUTSIDE
    assert platform.ads == {"ad:1": False, "ad:2": True}
    rows = await SqlChangeRepository(sessions, BUSINESS).all()
    assert not [r for r in rows if r.source is ChangeSource.ROLLBACK]


async def test_partly_reverted_request_reverts_the_rest(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    platform = two_campaigns()
    ledger = make(platform, sessions, engine)
    first = await done(ledger, user_pause("ad:1", "r1"))
    await done(ledger, user_pause("ad:2", "r1"))
    await rollback_change(ledger, first)

    result = await rollback_request(ledger, first)

    assert result.status is RollbackStatus.APPLIED
    assert platform.ads == {"ad:1": True, "ad:2": True}
