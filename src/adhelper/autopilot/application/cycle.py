import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from adhelper.autopilot.domain.decide import Action, ActionKind, Mode, plan_cycle
from adhelper.autopilot.domain.explain import explain
from adhelper.autopilot.domain.health import HealthInput, check_health
from adhelper.autopilot.domain.model import AccountData, Verdict
from adhelper.autopilot.domain.plan_ev import plan_cycle_ev
from adhelper.autopilot.domain.rules import AutopilotRules

__all__ = ["AccountData", "AutopilotCycle", "Mode", "TestPlanner"]

logger = logging.getLogger(__name__)

DATA_ALERT = "data_untrusted"
# Теневые записи autopilot-3 живут под своими ссылками и статусом: действий и карточек у них нет,
# а решения рабочих правил по тем же объектам они не заслоняют.
SHADOW_STATUS = "shadow"
SHADOW_PREFIX = "shadow:"
# До калибровки порогов на пилоте (T7) автопилот только предлагает: решает владелец.
SCHEDULED_MODE = Mode.CONFIRM


class AutopilotData(Protocol):
    async def load(self, account: str, now: datetime) -> AccountData: ...


class DecisionLog(Protocol):
    async def record(
        self,
        account: str,
        object_ref: str,
        verdict: str,
        action: str | None,
        status: str,
        inputs: Mapping[str, Any],
        rules_version: str,
        decided_at: datetime,
    ) -> int: ...


class Actions(Protocol):
    async def pause_ad(
        self, account: str, ad_ref: str, explanation: str, decision_id: int
    ) -> str: ...
    async def increase_limit(self, account: str, campaign_id: str, new_limit: int) -> str: ...


class TestPlanner(Protocol):
    """Гипотеза по фактам профиля и подготовка теста. docs/04-autopilot.md §5."""

    async def hypothesis(self, account: str, ad_ref: str) -> str | None: ...
    async def prepare(
        self, account: str, campaign_id: str, ad_ref: str, hypothesis: str, decision_id: int
    ) -> str: ...


class DataAlerts(Protocol):
    async def raise_alert(self, kind: str, details: Mapping[str, Any]) -> None: ...
    async def resolve(self, kind: str) -> None: ...


class AutopilotCycle:
    """Данные → проверка качества → оценка → предложения → журнал и выполнение."""

    def __init__(
        self,
        data: AutopilotData,
        decisions: DecisionLog,
        actions: Actions,
        alerts: DataAlerts,
        clock: Callable[[], datetime],
        rules: AutopilotRules,
        tests: TestPlanner | None = None,
        shadow_rules: AutopilotRules | None = None,
    ) -> None:
        self._shadow_rules = shadow_rules
        self._tests = tests
        self._data = data
        self._decisions = decisions
        self._actions = actions
        self._alerts = alerts
        self._clock = clock
        self._rules = rules

    async def run(
        self,
        account: str,
        target: int,
        mode: Mode,
        allowed: frozenset[ActionKind] | None = None,
    ) -> None:
        """`allowed` — что вправе сделать сам режим «Сам»; остальное только предлагается."""
        now = self._clock()
        data = await self._data.load(account, now)
        health = check_health(
            HealthInput(data.recent, data.baseline, data.platform_incident, data.manual_hold),
            self._rules,
        )
        if not health.trusted:
            reasons = [reason.value for reason in health.reasons]
            await self._alerts.raise_alert(
                DATA_ALERT, {"reasons": reasons, "rules_version": self._rules.version}
            )
            await self._decisions.record(
                account,
                account,
                "untrusted",
                None,
                "protect_only",
                {"reasons": reasons},
                self._rules.version,
                now,
            )
            await self._shadow(account, data, target, mode, now)
            return
        await self._alerts.resolve(DATA_ALERT)
        plan = plan_cycle(data.ads, data.campaigns, target, health, mode, now, self._rules)
        acted = {action.ref for action in plan.actions}
        for ref, evaluation in plan.evaluations.items():
            if ref not in acted:
                status = plan.blocked[ref].value if ref in plan.blocked else "no_action"
                await self._decisions.record(
                    account,
                    ref,
                    evaluation.verdict.value,
                    None,
                    status,
                    {
                        "p_good_bp": evaluation.posterior.p_good_bp,
                        "p_bad_bp": evaluation.posterior.p_bad_bp,
                    },
                    self._rules.version,
                    now,
                )
        for action in plan.actions:
            if allowed is not None and action.kind not in allowed:
                action = replace(action, needs_confirmation=True)
            await self._execute(account, action, now)
        await self._shadow(account, data, target, mode, now)

    async def _shadow(
        self, account: str, data: AccountData, target: int, mode: Mode, now: datetime
    ) -> None:
        """Правила autopilot-3 считают рядом и пишут «сделала бы»: ничего не выполняется.

        Сбой теневого счёта не должен мешать рабочему циклу, поэтому ловим всё и пишем в лог
        только тип ошибки.
        """
        rules = self._shadow_rules
        if rules is None:
            return
        try:
            health = check_health(
                HealthInput(data.recent, data.baseline, data.platform_incident, data.manual_hold),
                rules,
            )
            if not health.trusted:
                reasons = [reason.value for reason in health.reasons]
                await self._decisions.record(
                    account,
                    SHADOW_PREFIX + account,
                    "untrusted",
                    None,
                    SHADOW_STATUS,
                    {"would": "protect_only", "reasons": reasons, "goal_micros": target},
                    rules.version,
                    now,
                )
                return
            plan = plan_cycle_ev(data.ads, data.campaigns, target, health, mode, now, rules)
            chosen = {action.ref: action for action in plan.actions}
            for ref, evaluation in plan.evaluations.items():
                action = chosen.get(ref)
                reason = (
                    "none"
                    if action
                    else (plan.blocked[ref].value if ref in plan.blocked else "no_action")
                )
                inputs: dict[str, Any] = dict(plan.assessments.get(ref, {})) | {
                    "would": action.kind.value if action else "wait",
                    "reason": reason,
                    "goal_micros": target,
                }
                if ref in plan.compared_with:
                    inputs["better_ref"] = plan.compared_with[ref]
                await self._decisions.record(
                    account,
                    SHADOW_PREFIX + ref,
                    evaluation.verdict.value,
                    action.kind.value if action else None,
                    SHADOW_STATUS,
                    inputs,
                    rules.version,
                    now,
                )
        except Exception as error:
            logger.warning("Теневой счёт %s не удался: %s", rules.version, type(error).__name__)

    async def _execute(self, account: str, action: Action, now: datetime) -> None:
        verdict = {
            ActionKind.PAUSE_AD: Verdict.INEFFICIENT,
            ActionKind.INCREASE_LIMIT: Verdict.EFFICIENT,
            ActionKind.START_TEST: Verdict.BORDERLINE,
        }[action.kind]
        if action.kind is ActionKind.START_TEST:
            await self._start_test(account, action, now)
            return
        if action.needs_confirmation:
            await self._log(account, action, verdict, "awaiting_confirmation", now)
            return
        if action.kind is ActionKind.PAUSE_AD:
            decision_id = await self._log(account, action, verdict, "planned", now)
            status = await self._actions.pause_ad(
                account, action.ref, explain(action.kind.value, action.facts), decision_id
            )
            await self._log(account, action, verdict, status, now)
            return
        if action.new_limit is None:
            raise ValueError("У увеличения лимита должно быть новое значение")
        status = await self._actions.increase_limit(account, action.ref, action.new_limit)
        await self._log(account, action, verdict, status, now)

    async def _start_test(self, account: str, action: Action, now: datetime) -> None:
        """Тест меняет показы, поэтому в режиме подтверждения ждёт согласия владельца."""
        verdict = Verdict.BORDERLINE
        if self._tests is None:
            await self._log(account, action, verdict, "test_needed", now)
            return
        hypothesis = await self._tests.hypothesis(account, action.ref)
        if hypothesis is None:
            await self._log(account, action, verdict, "no_hypothesis", now)
            return
        extra = {"hypothesis": hypothesis, "campaign_id": action.campaign_id}
        if action.needs_confirmation:
            await self._log(account, action, verdict, "awaiting_confirmation", now, extra)
            return
        decision_id = await self._log(account, action, verdict, "planned", now, extra)
        status = await self._tests.prepare(
            account, action.campaign_id, action.ref, hypothesis, decision_id
        )
        await self._log(account, action, verdict, status, now, extra)

    async def _log(
        self,
        account: str,
        action: Action,
        verdict: Verdict,
        status: str,
        now: datetime,
        extra: Mapping[str, str] | None = None,
    ) -> int:
        inputs: dict[str, Any] = dict(action.facts) | dict(extra or {})
        if action.new_limit is not None:
            inputs["new_limit_micros"] = action.new_limit
        return await self._decisions.record(
            account,
            action.ref,
            verdict.value,
            action.kind.value,
            status,
            inputs,
            action.rules_version,
            now,
        )
