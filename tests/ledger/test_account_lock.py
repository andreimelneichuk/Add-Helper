"""Блокировка аккаунта исполнителя: не должна «залипать» за пулером соединений."""

from sqlalchemy.ext.asyncio import AsyncEngine

from adhelper.ledger.adapters.sql import PostgresAccountLock


async def test_second_holder_waits_and_lock_is_released(engine: AsyncEngine) -> None:
    lock = PostgresAccountLock(engine)
    async with lock.hold("acc-lock") as first:
        assert first is True
        async with lock.hold("acc-lock") as second:
            assert second is False
        async with lock.hold("other-account") as other:
            assert other is True
    async with lock.hold("acc-lock") as again:
        assert again is True


async def test_lock_is_released_after_error(engine: AsyncEngine) -> None:
    lock = PostgresAccountLock(engine)
    try:
        async with lock.hold("acc-err") as acquired:
            assert acquired is True
            raise RuntimeError("сбой внутри блокировки")
    except RuntimeError:
        pass
    async with lock.hold("acc-err") as again:
        assert again is True
