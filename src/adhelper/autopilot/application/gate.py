"""Ворота перед самостоятельным действием: политика, затем рецензент.

Отказ ворот не отменяет решение, а меняет его вид: действие уходит карточкой владельцу.
Любой сбой самих ворот тоже карточка: неизвестность никогда не разрешает действие.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from adhelper.autopilot.domain.decide import Action, ActionKind
from adhelper.autopilot.domain.model import AccountData
from adhelper.autopilot.domain.policy import (
    PolicyAction,
    PolicyInput,
    PolicyRules,
    check_policy,
)
from adhelper.autopilot.domain.review import Candidate, Reviewer

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


class GateFactsSource(Protocol):
    async def load(self, account: str, now: datetime) -> GateFacts: ...


@dataclass(frozen=True)
class GateNote:
    """Исход ворот по одному действию: пропущено или задержано, и что писать в журнал."""

    held: bool
    notes: dict[str, str]


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
        except Exception as error:
            logger.warning("Ворота не получили факты: %s", type(error).__name__)
            return {a.ref: GateNote(True, version | {"held_by": "gate_error"}) for a in todo}
        notes: dict[str, GateNote] = {}
        passed: list[Action] = []
        permitted = frozenset(POLICY_ACTION[kind] for kind in (allowed or frozenset(ActionKind)))
        for action in todo:
            try:
                decision = check_policy(
                    self._input(action, data, facts, permitted, now), self._rules
                )
            except Exception as error:
                logger.warning("Политика не посчитана: %s", type(error).__name__)
                notes[action.ref] = GateNote(True, version | {"held_by": "gate_error"})
                continue
            if decision.reason is not None:
                notes[action.ref] = GateNote(True, version | {"held_by": decision.reason.value})
            else:
                passed.append(action)
        if passed and self._reviewer is not None:
            result = await self._reviewer.review([self._candidate(a) for a in passed])
            for action in passed:
                if action.ref in result.passed:
                    notes[action.ref] = GateNote(False, version | {"review": "ok"})
                    continue
                held_by = "review_doubt" if result.available else "review_unavailable"
                reason = result.doubts.get(action.ref, "")
                notes[action.ref] = GateNote(
                    True, version | {"held_by": held_by, "review": reason[:200]}
                )
        else:
            for action in passed:
                notes[action.ref] = GateNote(False, version | {"review": "off"})
        return notes

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
            changes_today=facts.changes_today,
            moved_this_week=facts.moved_this_week,
            amount=amount,
            operating_budget=facts.operating_budget,
            active_ads_in_group=active_in_group,
            ad_in_learning=bool(ad and ad.age_days < self._rules.ad_learning_days),
            campaign_in_learning=campaign.in_learning if campaign else None,
            auto_strategy=campaign.auto_strategy if campaign else None,
            last_limit_change_at=campaign.last_change_at if campaign else None,
            data_alert=facts.data_alert,
            api_points_rest=facts.api_points_rest,
        )
