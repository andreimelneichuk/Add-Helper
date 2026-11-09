from typing import Literal

from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import ChangeKind, ChangeSource, ChangeStatus, ProposedChange
from adhelper.ledger.domain.ports import GatewayUnavailable

AD_PREFIX = "ad:"
OwnerResult = Literal["applied", "pending", "failed"]


async def set_ad_active(
    ledger: Ledger, account: str, ad_id: str, title: str, active: bool, request_id: str
) -> OwnerResult | None:
    """Просьба владельца тем же путём, что у автопилота: журнал → исполнитель → чтение.

    None значит, что площадка не ответила; запись остаётся, и повтор начнётся с чтения.
    """
    target_ref = f"{AD_PREFIX}{ad_id}"
    key = f"owner:{request_id}"
    # Индекс журнала держит ключ только у незавершённых записей, поэтому готовую ищем сами.
    rows = await ledger.changes_on_target(account, target_ref)
    done = next((row for row in rows if row.idempotency_key == key), None)
    if done is not None and done.status is ChangeStatus.APPLIED:
        return "applied"
    try:
        kind = ChangeKind.RESUME_AD if active else ChangeKind.PAUSE_AD
        # Повтор незавершённого действия продолжает ту же запись, даже если клиент
        # после таймаута прислал новый request_id. Завершённые действия не склеиваем:
        # владелец может позже снова включить или отключить объявление.
        change = next(
            (
                row
                for row in rows
                if row.source is ChangeSource.USER
                and row.kind is kind
                and row.planned.get("active") is active
                and row.status in {ChangeStatus.PLANNED, ChangeStatus.APPLYING}
            ),
            None,
        )
        if change is None:
            # Между действиями другой вкладки целевое состояние могло уже
            # измениться. Не создаём новую строку журнала, если запрос уже
            # соответствует фактическому состоянию объявления.
            current = await ledger.read_target(account, target_ref)
            if current.get("active") is active:
                return "applied"
            change = await ledger.submit(
                ProposedChange(
                    account=account,
                    source=ChangeSource.USER,
                    kind=kind,
                    target_ref=target_ref,
                    planned={"active": active},
                    explanation=f"{'Включил' if active else 'Отключил'} «{title}»: вы попросили.",
                    idempotency_key=key,
                )
            )
        await ledger.execute_pending(account)
    except GatewayUnavailable:
        return None
    status = await ledger.status_of(change.id)
    if status is ChangeStatus.APPLIED:
        return "applied"
    if status in {ChangeStatus.PLANNED, ChangeStatus.APPLYING}:
        return "pending"
    return "failed"
