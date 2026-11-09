from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from adhelper.autopilot.domain.health import HealthReport
from adhelper.autopilot.domain.model import AdStats, CampaignStats, Evaluation, Verdict, classify
from adhelper.autopilot.domain.rules import AutopilotRules
from adhelper.budget.domain.rules import floor_share


class Mode(StrEnum):
    AUTO = "auto"
    CONFIRM = "confirm"
    PAUSE = "pause"


class ActionKind(StrEnum):
    PAUSE_AD = "pause_ad"
    INCREASE_LIMIT = "increase_limit"
    START_TEST = "start_test"


class BlockReason(StrEnum):
    LAST_AD_IN_GROUP = "last_ad_in_group"
    CHANGED_RECENTLY = "changed_recently"
    ONE_CHANGE_PER_CYCLE = "one_change_per_cycle"
    TEST_RUNNING = "test_running"
    ROLLED_BACK = "rolled_back"
    BUDGET_TOO_SMALL = "budget_too_small"
    TEST_DECLINED = "test_declined"
    # autopilot-3: почему решения нет.
    LOW_DATA = "low_data"
    WAIT_IS_CHEAPER = "wait_is_cheaper"
    NOT_WORSE = "not_worse"
    TOO_MANY_VARIANTS = "too_many_variants"
    STRATEGY_LEARNING = "strategy_learning"


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    ref: str
    campaign_id: str
    facts: Mapping[str, int]
    rules_version: str
    needs_confirmation: bool
    new_limit: int | None = None


@dataclass(frozen=True)
class CyclePlan:
    protect_only: bool
    actions: list[Action] = field(default_factory=list)
    evaluations: dict[str, Evaluation] = field(default_factory=dict)
    blocked: dict[str, BlockReason] = field(default_factory=dict)
    # Вся арифметика решения по объявлению: пишется в журнал целиком (autopilot-3).
    assessments: dict[str, Mapping[str, int]] = field(default_factory=dict)
    # С каким вариантом сравнивали: слабый → сильный.
    compared_with: dict[str, str] = field(default_factory=dict)


def _facts(ad: AdStats, evaluation: Evaluation, target: int) -> dict[str, int]:
    return {
        "spend_micros": ad.spend,
        "leads": ad.leads,
        "target_micros": target,
        "p_good_bp": evaluation.posterior.p_good_bp,
        "p_bad_bp": evaluation.posterior.p_bad_bp,
    }


def _plan_campaign(
    ads: Sequence[AdStats],
    campaign: CampaignStats | None,
    evaluations: Mapping[str, Evaluation],
    target: int,
    now: datetime,
    rules: AutopilotRules,
    needs_confirmation: bool,
) -> tuple[Action | None, dict[str, BlockReason]]:
    """Не больше одного изменения на кампанию за цикл: сначала убираем плохое."""
    blocked: dict[str, BlockReason] = {}
    bad = [ad for ad in ads if evaluations[ad.ref].verdict is Verdict.INEFFICIENT]
    good = [ad for ad in ads if evaluations[ad.ref].verdict is Verdict.EFFICIENT]
    middle = [ad for ad in ads if evaluations[ad.ref].verdict is Verdict.BORDERLINE]

    # Пограничные тоже ждут: тест поверх идущего теста или свежего изменения смешал бы данные.
    waiting = [ad.ref for ad in (*bad, *middle)]
    if campaign is not None and campaign.test_running:
        return None, dict.fromkeys(waiting, BlockReason.TEST_RUNNING)
    gap = timedelta(days=rules.min_change_gap_days)
    if campaign and campaign.last_change_at and now - campaign.last_change_at < gap:
        return None, dict.fromkeys(waiting, BlockReason.CHANGED_RECENTLY)

    running = Counter(ad.group_id for ad in ads)
    chosen: Action | None = None
    for ad in sorted(bad, key=lambda item: (-item.spend, item.ref)):
        if ad.rollback_hold:
            # Пользователь отменил это решение: автопилот не повторяет его сам.
            blocked[ad.ref] = BlockReason.ROLLED_BACK
        elif running[ad.group_id] <= 1:
            blocked[ad.ref] = BlockReason.LAST_AD_IN_GROUP
        elif chosen is not None:
            blocked[ad.ref] = BlockReason.ONE_CHANGE_PER_CYCLE
        else:
            facts = _facts(ad, evaluations[ad.ref], target)
            chosen = Action(
                ActionKind.PAUSE_AD,
                ad.ref,
                ad.campaign_id,
                facts,
                rules.version,
                needs_confirmation,
            )
    if chosen is not None:
        return chosen, blocked
    if good and campaign is not None and campaign.budget_limited:
        best = max(good, key=lambda item: (evaluations[item.ref].posterior.p_good_bp, item.ref))
        increase = floor_share(campaign.limit, rules.budget_increase_bp)
        facts = _facts(best, evaluations[best.ref], target) | {"limit_micros": campaign.limit}
        return (
            Action(
                ActionKind.INCREASE_LIMIT,
                campaign.campaign_id,
                campaign.campaign_id,
                facts,
                rules.version,
                needs_confirmation,
                campaign.limit + increase,
            ),
            blocked,
        )
    test = _plan_test(middle, campaign, evaluations, target, rules, blocked)
    if test is not None:
        facts, chosen_ad = test
        return (
            Action(
                ActionKind.START_TEST,
                chosen_ad.ref,
                chosen_ad.campaign_id,
                facts,
                rules.version,
                needs_confirmation,
            ),
            blocked,
        )
    return None, blocked


def _plan_test(
    middle: Sequence[AdStats],
    campaign: CampaignStats | None,
    evaluations: Mapping[str, Evaluation],
    target: int,
    rules: AutopilotRules,
    blocked: dict[str, BlockReason],
) -> tuple[dict[str, int], AdStats] | None:
    """Тест — только когда лимит за срок теста покрывает несколько цен обращения.

    Иначе за срок теста обращений будет так мало, что вывода не получится, а деньги уйдут.
    """
    if not middle:
        return None
    limit = campaign.limit if campaign is not None else 0
    if limit * rules.test_days < rules.test_min_target_multiple * target:
        blocked.update(dict.fromkeys((ad.ref for ad in middle), BlockReason.BUDGET_TOO_SMALL))
        return None
    for ad in sorted(middle, key=lambda item: item.ref):
        if ad.test_hold:
            blocked[ad.ref] = BlockReason.TEST_DECLINED
            continue
        facts = _facts(ad, evaluations[ad.ref], target)
        if campaign is not None:
            facts["limit_micros"] = campaign.limit
        return facts, ad
    return None


def plan_cycle(
    ads: Sequence[AdStats],
    campaigns: Sequence[CampaignStats],
    target: int,
    health: HealthReport,
    mode: Mode,
    now: datetime,
    rules: AutopilotRules,
) -> CyclePlan:
    """Детерминированное решение: одинаковый вход всегда даёт одинаковый план."""
    if not health.trusted:
        return CyclePlan(protect_only=True)
    active = [ad for ad in ads if ad.active]
    evaluations = {ad.ref: classify(ad, target, rules) for ad in active}
    plan = CyclePlan(protect_only=False, evaluations=evaluations)
    if mode is Mode.PAUSE:
        return plan
    by_id = {campaign.campaign_id: campaign for campaign in campaigns}
    for campaign_id in sorted({ad.campaign_id for ad in active}):
        action, blocked = _plan_campaign(
            [ad for ad in active if ad.campaign_id == campaign_id],
            by_id.get(campaign_id),
            evaluations,
            target,
            now,
            rules,
            mode is Mode.CONFIRM,
        )
        plan.blocked.update(blocked)
        if action is not None:
            plan.actions.append(action)
    return plan
