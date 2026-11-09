"""Ворота перед самостоятельным действием: политика, затем рецензент.

Отказ ворот не отменяет решение, а меняет его вид: действие уходит карточкой владельцу.
Любой сбой самих ворот тоже карточка: неизвестность никогда не разрешает действие.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from adhelper.autopilot.domain.decide import Action, ActionKind
from adhelper.autopilot.domain.model import AccountData, CampaignStats
from adhelper.autopilot.domain.policy import (
    PolicyAction,
    PolicyInput,
    PolicyRules,
    check_policy,
)
from adhelper.autopilot.domain.review import Candidate, Reviewer, nothing_passed

logger = logging.getLogger(__name__)

POLICY_ACTION = {
    ActionKind.PAUSE_AD: PolicyAction.PAUSE_AD,
    ActionKind.INCREASE_LIMIT: PolicyAction.INCREASE_LIMIT,
    ActionKind.START_TEST: PolicyAction.START_TEST,
}


@dataclass(frozen=True)
class GateFacts:
    """Факты кабинета для политики: что автопилот уже сделал и чем располагает."""

    changes_today: int
    moved_this_week: int
    data_alert: bool
    api_points_rest: int | None
    operating_budget: int
    # Последнее изменение лимита кампании по журналу (рост или снижение, в том числе донором).
    last_limit_change: Mapping[str, datetime] = field(default_factory=dict)


class GateFactsSource(Protocol):
    async def load(self, account: str, now: datetime) -> GateFacts: ...


@dataclass(frozen=True)
class GateNote:
    """Исход ворот по одному действию: пропущено или задержано, и что писать в журнал."""

    held: bool
    notes: dict[str, str]


class Gate(Protocol):
    async def screen(
        self,
        account: str,
        actions: Sequence[Action],
        data: AccountData,
        allowed: frozenset[ActionKind] | None,
        now: datetime,
    ) -> dict[str, GateNote]: ...


class ClosedGate:
    """Ворота без правил: файл политики не прочитан, поэтому самостоятельно не делаем ничего."""

    async def screen(
        self,
        account: str,
        actions: Sequence[Action],
        data: AccountData,
        allowed: frozenset[ActionKind] | None,
        now: datetime,
    ) -> dict[str, GateNote]:
        note = {"policy_version": "unavailable", "held_by": "gate_error"}
        return {a.ref: GateNote(True, dict(note)) for a in actions if not a.needs_confirmation}


class PolicyGate:
    def __init__(
        self, rules: PolicyRules, facts: GateFactsSource, reviewer: Reviewer | None = None
    ) -> None:
        self._rules = rules
        self._facts = facts
        self._reviewer = reviewer

    async def screen(
        self,
        account: str,
        actions: Sequence[Action],
        data: AccountData,
        allowed: frozenset[ActionKind] | None,
        now: datetime,
    ) -> dict[str, GateNote]:
        """Ключ — `ref` действия. Действия без `needs_confirmation` проверяются здесь."""
        version = {"policy_version": self._rules.version}
        todo = [action for action in actions if not action.needs_confirmation]
        if not todo:
            return {}
        try:
            facts = await self._facts.load(account, now)
            # Пустой набор разрешений не значит «всё»: всё разрешено только при `None`.
            kinds = frozenset(ActionKind) if allowed is None else allowed
            permitted = frozenset(POLICY_ACTION[kind] for kind in kinds)
        except Exception as error:
            logger.warning("Ворота не получили факты: %s", type(error).__name__)
            return {a.ref: GateNote(True, version | {"held_by": "gate_error"}) for a in todo}
        notes: dict[str, GateNote] = {}
        passed: list[Action] = []
        # Счётчики копятся внутри цикла: каждое пропущенное действие занимает место следующим.
        changes, moved = facts.changes_today, facts.moved_this_week
        for action in todo:
            try:
                inp = self._input(action, data, facts, permitted, now, changes, moved)
                decision = check_policy(inp, self._rules)
            except Exception as error:
                logger.warning("Политика не посчитана: %s", type(error).__name__)
                self._put(notes, action.ref, GateNote(True, version | {"held_by": "gate_error"}))
                continue
            if decision.reason is not None:
                self._put(
                    notes, action.ref, GateNote(True, version | {"held_by": decision.reason.value})
                )
                continue
            passed.append(action)
            changes += 1
            moved += max(inp.amount, 0)
        if passed and self._reviewer is not None:
            try:
                result = await self._reviewer.review([self._candidate(a) for a in passed])
            except Exception as error:
                logger.warning("Рецензент не отработал: %s", type(error).__name__)
                result = nothing_passed(
                    [self._candidate(a) for a in passed], "рецензент недоступен", False
                )
            for action in passed:
                if action.ref in result.passed:
                    self._put(notes, action.ref, GateNote(False, version | {"review": "ok"}))
                    continue
                held_by = "review_doubt" if result.available else "review_unavailable"
                reason = result.doubts.get(action.ref, "")
                self._put(
                    notes,
                    action.ref,
                    GateNote(True, version | {"held_by": held_by, "review": reason[:200]}),
                )
        else:
            for action in passed:
                self._put(notes, action.ref, GateNote(False, version | {"review": "off"}))
        return notes

    @staticmethod
    def _put(notes: dict[str, GateNote], ref: str, note: GateNote) -> None:
        """Одинаковый `ref` у двух действий: любой отказ сильнее пропуска."""
        existing = notes.get(ref)
        if existing is not None and existing.held and not note.held:
            return
        notes[ref] = note

    def _candidate(self, action: Action) -> Candidate:
        return Candidate(
            action.ref, action.kind.value, action.campaign_id, action.facts, action.new_limit
        )

    def _input(
        self,
        action: Action,
        data: AccountData,
        facts: GateFacts,
        permitted: frozenset[PolicyAction],
        now: datetime,
        changes_today: int,
        moved_this_week: int,
    ) -> PolicyInput:
        campaign = next((c for c in data.campaigns if c.campaign_id == action.campaign_id), None)
        ad = next((a for a in data.ads if a.ref == action.ref), None)
        group = ad.group_id if ad else None
        active_in_group = (
            sum(1 for a in data.ads if a.group_id == group and a.active) if ad else None
        )
        amount = 0
        if action.new_limit is not None:
            amount = action.new_limit - (campaign.limit if campaign else action.new_limit)
        return PolicyInput(
            action=POLICY_ACTION[action.kind],
            ref=action.ref,
            campaign_id=action.campaign_id,
            allowed=permitted,
            now=now,
            changes_today=changes_today,
            moved_this_week=moved_this_week,
            amount=amount,
            operating_budget=facts.operating_budget,
            active_ads_in_group=active_in_group,
            # Объявление не нашли: возраст неизвестен, а паузе нужен известный возраст.
            ad_in_learning=ad is None and action.kind is ActionKind.PAUSE_AD,
            ad_age_days=ad.age_days if ad else None,
            campaign_in_learning=campaign.in_learning if campaign else None,
            auto_strategy=campaign.auto_strategy if campaign else None,
            last_limit_change_at=self._last_change(campaign, facts),
            data_alert=facts.data_alert or data.platform_incident or data.manual_hold,
            api_points_rest=facts.api_points_rest,
        )

    @staticmethod
    def _last_change(campaign: CampaignStats | None, facts: GateFacts) -> datetime | None:
        """Самое позднее из известных изменений лимита: рост, снижение донором, журнал."""
        if campaign is None:
            return None
        known = [
            t
            for t in (campaign.last_change_at, facts.last_limit_change.get(campaign.campaign_id))
            if t
        ]
        return max(known) if known else None
