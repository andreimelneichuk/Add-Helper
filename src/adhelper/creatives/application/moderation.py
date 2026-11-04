"""Отправка объявлений в Директ и итог проверки: docs/05-creatives.md §8.

Всё, что меняет площадку, идёт через журнал: создание объявления (`create_ad`) и остановка
заменённого (`pause_ad`). Повтор начинается с чтения: созданное находится по метке,
второй раз не создаётся. Проверку Яндекса только читаем.
"""

import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from adhelper.budget.domain.ports import PlatformUnavailable
from adhelper.creatives.domain.moderation import (
    Checking,
    ModerationPlan,
    ModerationRules,
    Rejection,
    ad_marker,
    plan_moderation,
)
from adhelper.creatives.domain.ports import Attention, ModerationStore, Outgoing
from adhelper.ledger.application.service import Ledger
from adhelper.ledger.domain.model import (
    Change,
    ChangeKind,
    ChangeSource,
    ChangeStatus,
    ProposedChange,
)
from adhelper.ledger.domain.ports import GatewayUnavailable
from adhelper.platforms.domain.ports import AdPublisher

logger = logging.getLogger(__name__)

CURRENT_MODERATION_RULES = "moderation-1"
NEW_AD_PREFIX = "newad:"
AD_PREFIX = "ad:"
REPLACED_KEY = "replaced:"
ATTENTION_KIND = "ads_rejected"

LedgerFor = Callable[[str], Ledger | None]
PublisherFor = Callable[[str], AdPublisher | None]


@dataclass(frozen=True)
class CycleResult:
    sent: int
    accepted: int
    rejected: int
    repairs: int
    to_owner: int


def _create(item: Outgoing) -> ProposedChange:
    return ProposedChange(
        account=item.account,
        source=ChangeSource.AUTOPILOT,
        kind=ChangeKind.CREATE_AD,
        target_ref=f"{NEW_AD_PREFIX}{ad_marker(item.creative_id)}",
        planned={
            "campaign_id": item.campaign_id,
            "headline": item.draft.headline,
            "text": item.draft.text,
            "href": item.href,
        },
        explanation=(
            f"Отправил на проверку Яндексу объявление «{item.draft.headline}»."
            " Почему: в направлении было свободное место или нужна замена."
            " Дальше: показы начнутся после одобрения, прежнее объявление работает до него."
            " Отменить: отключите объявление на экране «Реклама»."
        ),
        idempotency_key=f"create-ad:{item.creative_id}",
    )


def _latest_applied(rows: Sequence[Change], kind: ChangeKind) -> Change | None:
    done = [row for row in rows if row.kind is kind and row.status is ChangeStatus.APPLIED]
    return max(done, key=lambda row: row.id) if done else None


async def send_ready(store: ModerationStore, ledger_for: LedgerFor, now: datetime) -> int:
    """Объявления, занявшие место, уходят на площадку. Возвращает число отправленных."""
    sent = 0
    stalled: set[str] = set()
    for item in await store.outgoing():
        ledger = ledger_for(item.account)
        if ledger is None or item.account in stalled:
            continue
        if not item.href:
            # Объявлению нужна страница; без неё ждём, пока у направления появится ссылка.
            continue
        proposed = _create(item)
        try:
            done = _latest_applied(
                await ledger.changes_on_target(item.account, proposed.target_ref),
                ChangeKind.CREATE_AD,
            )
            if done is None:
                await ledger.submit(proposed)
                await ledger.execute_pending(item.account)
                done = _latest_applied(
                    await ledger.changes_on_target(item.account, proposed.target_ref),
                    ChangeKind.CREATE_AD,
                )
        except GatewayUnavailable:
            # Запись осталась в журнале: согласование или следующий цикл доведут её чтением.
            stalled.add(item.account)
            continue
        if done is None or not done.after or "ad_id" not in done.after:
            continue
        if await store.sent(item.creative_id, str(done.after["ad_id"]), item.href, now):
            sent += 1
    return sent


async def _pause_replaced(
    store: ModerationStore, ledger: Ledger, account: str, ad_id: str, title: str, now: datetime
) -> None:
    key = f"{REPLACED_KEY}{ad_id}"
    try:
        await ledger.submit(
            ProposedChange(
                account=account,
                source=ChangeSource.AUTOPILOT,
                kind=ChangeKind.PAUSE_AD,
                target_ref=f"{AD_PREFIX}{ad_id}",
                planned={"active": False},
                explanation=(
                    f"Отключил «{title}»: Яндекс одобрил объявление, которое его заменяет."
                    " Почему: в старом тексте устаревший факт или его переделали по просьбе."
                    " Отменить: включите объявление на экране «Реклама»."
                ),
                idempotency_key=key,
            )
        )
        await ledger.execute_pending(account)
    except GatewayUnavailable:
        return
    await _finish_replaced(store, ledger, account, now)


async def _finish_replaced(
    store: ModerationStore, ledger: Ledger, account: str, now: datetime
) -> None:
    """Остановки заменённых, которые журнал уже применил, в том числе после сбоя."""
    for change in await ledger.changes_with_key_prefix(account, REPLACED_KEY):
        if change.status is ChangeStatus.APPLIED:
            await store.replaced(account, change.target_ref.removeprefix(AD_PREFIX), now)


# Снятие и включение объявления владельцем или отменой: решение человека, а не автопилота.
OWNER_SOURCES = (ChangeSource.USER, ChangeSource.ROLLBACK)
OWNER_KINDS = (ChangeKind.PAUSE_AD, ChangeKind.RESUME_AD)


async def follow_owner(store: ModerationStore, ledger: Ledger, account: str, now: datetime) -> None:
    """Строки объявлений следуют за журналом: снятое владельцем освобождает место,
    отмена снятия возвращает объявление на проверку. Повтор безопасен."""
    for ad_id, state, reason in await store.on_platform(account):
        rows = await ledger.changes_on_target(account, f"{AD_PREFIX}{ad_id}")
        done = [
            row
            for row in rows
            if row.kind in OWNER_KINDS
            and row.source in OWNER_SOURCES
            and row.status is ChangeStatus.APPLIED
        ]
        if not done:
            continue
        last = max(done, key=lambda row: row.id)
        stopped = state == "stopped" and reason == "owner_stopped"
        if last.kind is ChangeKind.PAUSE_AD and not stopped:
            await store.owner_stopped(account, ad_id, now)
        elif last.kind is ChangeKind.RESUME_AD and stopped:
            await store.owner_resumed(account, ad_id, now)


async def check_moderation(
    store: ModerationStore,
    publisher_for: PublisherFor,
    ledger_for: LedgerFor,
    attention: Attention,
    rules: ModerationRules,
    creative_rules: str,
    rework_rules: str,
    now: datetime,
) -> ModerationPlan:
    """Читает итог проверки и решает: показы, ремонт или владелец. Повтор безопасен."""
    known = sorted(set(await store.accounts()))
    for account in known:
        ledger = ledger_for(account)
        if ledger is not None:
            # Сначала решения владельца: снятое не проверяем, включённое проверим сейчас же.
            await follow_owner(store, ledger, account, now)
    by_account: dict[str, list[Checking]] = defaultdict(list)
    for ad in await store.checking():
        by_account[ad.account].append(ad)
    accepted: list[Checking] = []
    rejected: list[Rejection] = []
    last = dict(await store.last_rejections())
    for account in sorted(set(by_account) | set(known)):
        ledger = ledger_for(account)
        ads = by_account.get(account, [])
        publisher = publisher_for(account)
        if ads and publisher is not None:
            try:
                verdicts = await publisher.moderation(account, [ad.ad_id for ad in ads])
            except PlatformUnavailable:
                verdicts = []
            plan = plan_moderation(ads, verdicts, last, rules)
            last.update({(r.ad.account, r.ad.campaign_id): r.kind.code for r in plan.rejected})
            to_pause = await store.apply(plan, rules, creative_rules, rework_rules, now)
            accepted.extend(plan.accepted)
            rejected.extend(plan.rejected)
            if ledger is not None:
                for old in to_pause:
                    await _pause_replaced(
                        store, ledger, account, old.ad_id, old.draft.headline, now
                    )
        if ledger is not None:
            # Остановки, которые журнал довёл после сбоя прошлого цикла.
            await _finish_replaced(store, ledger, account, now)
    waiting = await store.waiting_owner()
    if waiting:
        kinds = {kind.code: kind for kind in rules.kinds}
        first = waiting[0]
        kind = kinds.get(first["reason"], rules.kinds[-1])
        await attention.raise_alert(
            ATTENTION_KIND,
            {"count": len(waiting), "headline": first["headline"], "reason": kind.owner_text},
        )
    else:
        await attention.resolve(ATTENTION_KIND)
    return ModerationPlan(tuple(accepted), tuple(rejected))
