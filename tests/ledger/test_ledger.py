import asyncio
import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adhelper.ledger.adapters.sql import (
    PostgresAccountLock,
    SqlChangeRepository,
    accounts_with_open_changes,
)
from adhelper.ledger.application.service import Ledger, reconcile_open_changes
from adhelper.ledger.domain.model import (
    Change,
    ChangeKind,
    ChangeSource,
    ChangeStatus,
    ProposedChange,
)
from tests.ledger.conftest import FakeGateway
from tests.seed import seed_account

BUSINESS = uuid.uuid4()
OTHER_BUSINESS = uuid.uuid4()


@pytest.fixture(autouse=True)
async def _owners(sessions: async_sessionmaker[AsyncSession]) -> None:
    await seed_account(sessions, BUSINESS, "acc", "acc-2", "other")
    await seed_account(sessions, OTHER_BUSINESS, "acc", "acc-2", "other")


Sessions = async_sessionmaker[AsyncSession]


class ProcessDied(BaseException):
    """Имитация падения процесса: обычная обработка ошибок её не перехватывает."""


def proposal(target: str = "a", limit: int = 400, key: str | None = None) -> ProposedChange:
    return ProposedChange(
        account="acc",
        source=ChangeSource.AUTOPILOT,
        kind=ChangeKind.SET_LIMIT,
        target_ref=target,
        planned={"limit": limit},
        explanation="Снижаем лимит",
        idempotency_key=key or f"{target}:{limit}",
    )


def new_ad_proposal() -> ProposedChange:
    return ProposedChange(
        account="acc",
        source=ChangeSource.USER,
        kind=ChangeKind.CREATE_AD,
        target_ref="newad:wizard-test",
        planned={
            "campaign_id": "123",
            "headline": "Title",
            "text": "Text",
            "href": "https://example.com/",
            "group_name": "Services",
            "keywords": ["service"],
        },
        explanation="Publish ad",
        idempotency_key="new-ad:test",
    )


class DirectLikeAdGateway:
    """Models ads.get, which omits the group name and keywords used by ads.add."""

    def __init__(self, state: dict[str, dict[str, Any]] | None = None) -> None:
        self.state = state or {}
        self.apply_calls: list[str] = []

    async def read(self, account: str, target_ref: str) -> Mapping[str, Any]:
        return dict(self.state.get(target_ref, {}))

    async def apply(
        self, account: str, kind: ChangeKind, target_ref: str, planned: Mapping[str, Any]
    ) -> None:
        self.apply_calls.append(target_ref)
        self.state[target_ref] = {
            "campaign_id": planned["campaign_id"],
            "headline": planned["headline"],
            "text": planned["text"],
            "href": planned["href"],
            "ad_id": "456",
            "group_id": "789",
        }


def ledger(
    sessions: Sessions, engine: AsyncEngine, gateway: Any, repo: Any = None, preflight: Any = None
) -> Ledger:
    return Ledger(
        repository=repo or SqlChangeRepository(sessions, BUSINESS),
        gateway=gateway,
        lock=PostgresAccountLock(engine),
        max_attempts=3,
        preflight=preflight,
    )


class CrashingGateway:
    def __init__(self, inner: FakeGateway, point: str) -> None:
        self.inner = inner
        self.point = point
        self.reads = 0

    async def read(self, account: str, target_ref: str) -> Mapping[str, Any]:
        self.reads += 1
        # Первое чтение при подаче заявки, второе перед применением, третье после него.
        if self.point == "before_read_result" and self.reads == 3:
            raise ProcessDied
        return await self.inner.read(account, target_ref)

    async def apply(
        self, account: str, kind: ChangeKind, target_ref: str, planned: Mapping[str, Any]
    ) -> None:
        if self.point == "before_apply":
            raise ProcessDied
        await self.inner.apply(account, kind, target_ref, planned)
        if self.point == "after_apply":
            raise ProcessDied


class CrashingRepository(SqlChangeRepository):
    def __init__(self, sessions: Sessions, point: str) -> None:
        super().__init__(sessions, BUSINESS)
        self.point = point

    async def mark_applying(self, change: Change) -> Change:
        if self.point == "before_mark_applying":
            raise ProcessDied
        result = await super().mark_applying(change)
        if self.point == "after_mark_applying":
            raise ProcessDied
        return result

    async def finish(
        self,
        change: Change,
        status: ChangeStatus,
        after: Mapping[str, Any] | None,
        reason: str | None = None,
    ) -> Change:
        if self.point == "before_finish":
            raise ProcessDied
        result = await super().finish(change, status, after, reason)
        if self.point == "after_finish":
            raise ProcessDied
        return result


CRASH_POINTS = [
    "before_mark_applying",
    "after_mark_applying",
    "before_apply",
    "after_apply",
    "before_read_result",
    "before_finish",
    "after_finish",
]


@pytest.mark.parametrize("point", CRASH_POINTS)
async def test_crash_at_any_step_never_applies_twice(
    sessions: Sessions, engine: AsyncEngine, point: str
) -> None:
    gateway = FakeGateway({"a": {"limit": 500}})
    crashing = ledger(
        sessions, engine, CrashingGateway(gateway, point), CrashingRepository(sessions, point)
    )
    await crashing.submit(proposal())

    with pytest.raises(ProcessDied):
        await crashing.execute_pending("acc")

    restarted = ledger(sessions, engine, gateway)
    await restarted.execute_pending("acc")

    changes = await SqlChangeRepository(sessions, BUSINESS).all()
    assert len(changes) == 1
    assert changes[0].status is ChangeStatus.APPLIED
    assert changes[0].before == {"limit": 500}
    assert changes[0].after == {"limit": 400}
    assert gateway.state["a"] == {"limit": 400}
    assert len(gateway.apply_calls) == 1


async def test_crash_after_submit_commit_is_recovered_by_reconcile(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    """Строка журнала и есть задание: она видна согласованию после перезапуска."""
    gateway = FakeGateway({"a": {"limit": 500}})
    await ledger(sessions, engine, gateway).submit(proposal())

    assert await accounts_with_open_changes(sessions) == [(BUSINESS, "acc")]
    await reconcile_open_changes(
        await accounts_with_open_changes(sessions),
        lambda business_id, account: ledger(sessions, engine, gateway),
    )

    assert gateway.state["a"] == {"limit": 400}
    assert await accounts_with_open_changes(sessions) == []


async def test_before_snapshot_is_written_before_platform_is_touched(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    gateway = FakeGateway({"a": {"limit": 500}})

    change = await ledger(sessions, engine, gateway).submit(proposal())

    assert change.status is ChangeStatus.PLANNED
    assert change.before == {"limit": 500}
    assert gateway.apply_calls == []


async def test_lost_response_finishes_without_second_apply(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    gateway = FakeGateway({"a": {"limit": 500}})
    gateway.lose_next_response = True
    service = ledger(sessions, engine, gateway)
    await service.submit(proposal())

    await service.execute_pending("acc")
    assert (await SqlChangeRepository(sessions, BUSINESS).all())[0].status is ChangeStatus.APPLYING

    await service.execute_pending("acc")
    assert (await SqlChangeRepository(sessions, BUSINESS).all())[0].status is ChangeStatus.APPLIED
    assert len(gateway.apply_calls) == 1


async def test_created_ad_finishes_when_direct_omits_creation_only_fields(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    gateway = DirectLikeAdGateway()
    service = ledger(sessions, engine, gateway)
    await service.submit(new_ad_proposal())

    await service.execute_pending("acc")

    change = (await SqlChangeRepository(sessions, BUSINESS).all())[0]
    assert change.status is ChangeStatus.APPLIED
    assert change.after == gateway.state["newad:wizard-test"]
    assert gateway.apply_calls == ["newad:wizard-test"]


async def test_reconcile_does_not_create_ad_already_visible_in_direct(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    target = "newad:wizard-test"
    gateway = DirectLikeAdGateway(
        {
            target: {
                "campaign_id": "123",
                "headline": "Title",
                "text": "Text",
                "href": "https://example.com/",
                "ad_id": "456",
                "group_id": "789",
            }
        }
    )
    repo = SqlChangeRepository(sessions, BUSINESS)
    await repo.add(new_ad_proposal(), {}, ChangeStatus.APPLYING)
    service = ledger(sessions, engine, gateway)

    await service.execute_pending("acc")

    change = (await repo.all())[0]
    assert change.status is ChangeStatus.APPLIED
    assert change.after == gateway.state[target]
    assert gateway.apply_calls == []


async def test_platform_down_keeps_change_open_then_gives_up(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    gateway = FakeGateway({"a": {"limit": 500}})
    service = ledger(sessions, engine, gateway)
    await service.submit(proposal())
    gateway.unavailable = True

    await service.execute_pending("acc")
    assert (await SqlChangeRepository(sessions, BUSINESS).all())[0].status is ChangeStatus.PLANNED

    # Площадка принимает вызов, но молча не применяет его.
    gateway.unavailable = False

    async def ignore(*_: Any) -> None:
        return None

    gateway.apply = ignore  # type: ignore[method-assign,assignment]
    for _ in range(4):
        await service.execute_pending("acc")

    change = (await SqlChangeRepository(sessions, BUSINESS).all())[0]
    assert change.status is ChangeStatus.FAILED
    assert change.attempts == 3


async def test_external_edit_is_not_overwritten(sessions: Sessions, engine: AsyncEngine) -> None:
    gateway = FakeGateway({"a": {"limit": 500}})
    service = ledger(sessions, engine, gateway)
    await service.submit(proposal())
    gateway.state["a"]["limit"] = 450

    await service.execute_pending("acc")

    change = (await SqlChangeRepository(sessions, BUSINESS).all())[0]
    assert change.status is ChangeStatus.CONFLICT
    assert change.after == {"limit": 450}
    assert gateway.apply_calls == []


async def test_changes_run_in_order_and_failure_blocks_the_next(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    """Иначе увеличение у получателя могло бы пройти раньше снижения у донора."""
    gateway = FakeGateway({"donor": {"limit": 500}, "recipient": {"limit": 100}})
    gateway.lose_next_response = True
    service = ledger(sessions, engine, gateway)
    await service.submit(proposal("donor", 400))
    await service.submit(proposal("recipient", 200))

    await service.execute_pending("acc")

    assert gateway.state["recipient"] == {"limit": 100}

    await service.execute_pending("acc")

    assert [call[0] for call in gateway.apply_calls] == ["donor", "recipient"]


async def test_second_worker_skips_locked_account(sessions: Sessions, engine: AsyncEngine) -> None:
    gateway = FakeGateway({"a": {"limit": 500}})
    service = ledger(sessions, engine, gateway)
    await service.submit(proposal())
    entered, release = asyncio.Event(), asyncio.Event()
    real_apply = gateway.apply

    async def slow_apply(*args: Any) -> None:
        entered.set()
        await release.wait()
        await real_apply(*args)

    gateway.apply = slow_apply  # type: ignore[method-assign,assignment]
    first = asyncio.create_task(service.execute_pending("acc"))
    await entered.wait()

    try:
        # Таймаут вместо зависания: без блокировки второй исполнитель ждал бы в slow_apply.
        await asyncio.wait_for(ledger(sessions, engine, gateway).execute_pending("acc"), timeout=5)
    finally:
        release.set()
        await first

    assert len(gateway.apply_calls) == 1


async def test_submit_with_open_key_returns_same_change(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    service = ledger(sessions, engine, FakeGateway({"a": {"limit": 500}}))

    first = await service.submit(proposal())
    second = await service.submit(proposal())

    assert first.id == second.id


async def test_finished_key_can_be_used_again(sessions: Sessions, engine: AsyncEngine) -> None:
    """Лимит вернули к прежнему значению: это новое изменение, а не повтор."""
    service = ledger(sessions, engine, FakeGateway({"a": {"limit": 500}}))
    first = await service.submit(proposal())
    await service.execute_pending("acc")

    second = await service.submit(proposal())

    assert first.id != second.id


async def test_rejection_is_recorded_and_never_executed(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    gateway = FakeGateway({"a": {"limit": 500}})
    service = ledger(sessions, engine, gateway)

    change = await service.record_rejection(proposal(), "exceeds_operating_budget")
    await service.execute_pending("acc")

    assert change.status is ChangeStatus.REJECTED
    assert change.failure_reason == "exceeds_operating_budget"
    assert gateway.apply_calls == []


async def test_repository_is_scoped_to_business(sessions: Sessions, engine: AsyncEngine) -> None:
    await ledger(sessions, engine, FakeGateway({"a": {"limit": 500}})).submit(proposal())

    other = SqlChangeRepository(sessions, OTHER_BUSINESS)

    assert await other.all() == []
    assert await other.pending("acc") == []


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE changes SET before = '{}'",
        "UPDATE changes SET planned = '{}'",
        "UPDATE changes SET explanation = 'другое'",
        "UPDATE changes SET business_id = gen_random_uuid()",
        "DELETE FROM changes",
        "TRUNCATE changes",
    ],
)
async def test_database_protects_journal(
    sessions: Sessions, engine: AsyncEngine, statement: str
) -> None:
    await ledger(sessions, engine, FakeGateway({"a": {"limit": 500}})).submit(proposal())

    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(text(statement))


async def test_database_forbids_leaving_final_status(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    service = ledger(sessions, engine, FakeGateway({"a": {"limit": 500}}))
    await service.submit(proposal())
    await service.execute_pending("acc")

    with pytest.raises(DBAPIError):
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE changes SET status = 'planned'"))


async def test_preflight_rejection_blocks_apply(sessions: Sessions, engine: AsyncEngine) -> None:
    gateway = FakeGateway({"a": {"limit": 500}})

    async def refuse(_: Change) -> str:
        return "exceeds_operating_budget"

    service = ledger(sessions, engine, gateway, preflight=refuse)
    await service.submit(proposal())

    await service.execute_pending("acc")

    change = (await SqlChangeRepository(sessions, BUSINESS).all())[0]
    assert change.status is ChangeStatus.FAILED
    assert change.failure_reason == "exceeds_operating_budget"
    assert gateway.apply_calls == []


async def test_pause_goes_ahead_of_queued_changes(sessions: Sessions, engine: AsyncEngine) -> None:
    gateway = FakeGateway({"a": {"limit": 500, "active": True}})
    service = ledger(sessions, engine, gateway)
    await service.submit(proposal("a", 600))
    await service.submit(
        ProposedChange(
            account="acc",
            source=ChangeSource.BUDGET_GUARD,
            kind=ChangeKind.PAUSE,
            target_ref="a",
            planned={"active": False},
            explanation="Остановка",
            idempotency_key="pause:a",
        )
    )

    await service.execute_pending("acc")

    assert [call[1] for call in gateway.apply_calls] == [{"active": False}, {"limit": 600}]


async def test_silently_ignored_decrease_blocks_the_increase(
    sessions: Sessions, engine: AsyncEngine
) -> None:
    """Площадка ответила успехом, но лимит донора не изменился: получатель расти не должен."""
    gateway = FakeGateway({"donor": {"limit": 500}, "recipient": {"limit": 100}})
    service = ledger(sessions, engine, gateway)
    await service.submit(proposal("donor", 400))
    await service.submit(proposal("recipient", 200))
    real_apply = gateway.apply

    async def ignore_donor(
        account: str, kind: ChangeKind, target_ref: str, planned: Mapping[str, Any]
    ) -> None:
        if target_ref != "donor":
            await real_apply(account, kind, target_ref, planned)

    gateway.apply = ignore_donor  # type: ignore[method-assign]
    await service.execute_pending("acc")
    assert gateway.state["recipient"] == {"limit": 100}

    gateway.apply = real_apply  # type: ignore[method-assign]
    await service.execute_pending("acc")
    assert gateway.state == {"donor": {"limit": 400}, "recipient": {"limit": 200}}
