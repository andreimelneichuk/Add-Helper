"""Решения autopilot-3: «действовать или ждать» в рублях, а не по одному порогу вероятности.

Для направления берутся два самых посещаемых варианта. Худший по оценке убирается, только если
выполнены все условия подряд: клики достаточны, интервал худшего ниже оценки лучшего, вероятность
«хуже» не ниже порога, а недополученное за неделю больше цены ошибки. Любое «нет» даёт причину
в `blocked`, действия нет. Плохие данные и медленное направление дают только защиту.
"""

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from adhelper.autopilot.domain.decide import Action, ActionKind, BlockReason, CyclePlan, Mode
from adhelper.autopilot.domain.health import HealthReport
from adhelper.autopilot.domain.loss import (
    belief,
    compare_rates,
    cost_interval_micros,
    expected_loss_micros,
    rate_interval,
    rate_mean,
)
from adhelper.autopilot.domain.model import (
    AdStats,
    CampaignStats,
    Evaluation,
    Posterior,
    Verdict,
)
from adhelper.autopilot.domain.rules import AutopilotRules

DAYS_IN_WEEK = 7
# Больше кликов у одного объявления не бывает: это сбой данных, а не успех.
MAX_CLICKS = 10**12


def observed_days(ad: AdStats, rules: AutopilotRules) -> int:
    """Сколько дней дозревших данных у объявления: свежие дни в клики и обращения не входят."""
    return max(1, ad.age_days - rules.maturity_days)


def _weekly(count: int, days: int) -> int:
    return count * DAYS_IN_WEEK // days


def _evaluation(verdict: Verdict, p_good_bp: int = 0, p_bad_bp: int = 0) -> Evaluation:
    return Evaluation(verdict, Posterior(p_good_bp, p_bad_bp))


def _plan_direction(
    ads: Sequence[AdStats],
    campaign: CampaignStats | None,
    target: int,
    now: datetime,
    rules: AutopilotRules,
    needs_confirmation: bool,
    plan: CyclePlan,
) -> None:
    refs = [ad.ref for ad in ads]
    days = max(observed_days(ad, rules) for ad in ads)
    weekly_leads = _weekly(sum(ad.leads for ad in ads), days)
    # Все входные данные решения пишутся сразу, в любой ветке: по записи оно воспроизводится.
    direction_clicks = sum(ad.clicks for ad in ads)
    direction_leads = sum(ad.leads for ad in ads)
    changed = campaign.last_change_at if campaign is not None else None
    base = {
        ad.ref: {
            "clicks": ad.clicks,
            "leads": ad.leads,
            "spend_micros": ad.spend,
            "age_days": ad.age_days,
            "observed_days": observed_days(ad, rules),
            "weekly_clicks": _weekly(ad.clicks, observed_days(ad, rules)),
            "weekly_leads": weekly_leads,
            "group_clicks": direction_clicks,
            "group_leads": direction_leads,
            "prior_clicks": rules.prior_clicks,
            "min_weekly_leads": rules.min_weekly_leads,
            "target_micros": target,
            "test_running": int(bool(campaign is not None and campaign.test_running)),
            "last_change_at": int(changed.timestamp()) if changed else 0,
            "rollback_hold": int(ad.rollback_hold),
        }
        for ad in ads
    }
    plan.assessments.update(base)
    if any(ad.leads > ad.clicks or ad.clicks > MAX_CLICKS for ad in ads):
        for ad in ads:
            plan.assessments[ad.ref] = base[ad.ref] | {"data_conflict": 1}
            plan.blocked[ad.ref] = BlockReason.DATA_CONFLICT
            plan.evaluations[ad.ref] = _evaluation(Verdict.LOW_DATA)
        return
    if weekly_leads < rules.min_weekly_leads:
        for ref in refs:
            plan.blocked[ref] = BlockReason.FEW_LEADS
            plan.evaluations[ref] = _evaluation(Verdict.LOW_DATA)
        return
    ranked = sorted(ads, key=lambda ad: (-ad.clicks, ad.ref))
    compared = ranked[: rules.max_variants]
    for ad in ranked[rules.max_variants :]:
        plan.blocked[ad.ref] = BlockReason.TOO_MANY_VARIANTS
        plan.evaluations[ad.ref] = _evaluation(Verdict.LOW_DATA)
    if len(compared) < 2:
        only = compared[0]
        plan.blocked[only.ref] = BlockReason.LAST_AD_IN_GROUP
        plan.evaluations[only.ref] = _evaluation(Verdict.LOW_DATA)
        return
    group_clicks = sum(ad.clicks for ad in compared)
    group_leads = sum(ad.leads for ad in compared)
    for ad in compared:
        plan.assessments[ad.ref] = base[ad.ref] | {
            "group_clicks": group_clicks,
            "group_leads": group_leads,
        }
    beliefs = {
        ad.ref: belief(ad.clicks, ad.leads, group_clicks, group_leads, rules.prior_clicks)
        for ad in compared
    }
    better, worse = sorted(
        compared, key=lambda ad: (-rate_mean(beliefs[ad.ref]), -ad.clicks, ad.ref)
    )
    plan.compared_with[worse.ref] = better.ref
    low, high = rate_interval(beliefs[worse.ref], rules.interval_tail_bp)
    cpl_low, cpl_high = cost_interval_micros(worse.spend, worse.clicks, low, high)
    pair = compare_rates(beliefs[worse.ref], beliefs[better.ref])
    weekly_clicks = _weekly(worse.clicks, observed_days(worse, rules))
    loss = expected_loss_micros(weekly_clicks, pair.expected_gap, target)
    error_cost = rules.error_cost_target_multiple * target
    if worse.age_days < rules.learning_days:
        error_cost *= rules.learning_error_multiple
    better_low, better_high = rate_interval(beliefs[better.ref], rules.interval_tail_bp)
    better_cpl = cost_interval_micros(better.spend, better.clicks, better_low, better_high)
    gap_ppm = math.floor(pair.expected_gap * 1_000_000)
    plan.assessments[worse.ref] = facts = dict(plan.assessments[worse.ref]) | {
        "p_worse_bp": pair.p_worse_bp,
        "expected_gap_ppm": gap_ppm,
        "loss_micros": loss,
        "error_cost_micros": error_cost,
        "cpl_low_micros": cpl_low,
        "cpl_high_micros": cpl_high,
        "better_clicks": better.clicks,
        "better_leads": better.leads,
        "better_cpl_low_micros": better_cpl[0],
        "better_cpl_high_micros": better_cpl[1],
    }
    plan.assessments[better.ref] = dict(plan.assessments[better.ref]) | {
        "p_worse_bp": pair.p_worse_bp,
        "expected_gap_ppm": gap_ppm,
        "cpl_low_micros": better_cpl[0],
        "cpl_high_micros": better_cpl[1],
    }

    def hold(reason: BlockReason, verdict: Verdict) -> None:
        plan.blocked[worse.ref] = reason
        plan.evaluations[worse.ref] = _evaluation(verdict, 0, pair.p_worse_bp)
        plan.evaluations[better.ref] = _evaluation(
            Verdict.LOW_DATA if verdict is Verdict.LOW_DATA else Verdict.BORDERLINE
        )
        if reason in (BlockReason.TEST_RUNNING, BlockReason.CHANGED_RECENTLY):
            plan.blocked[better.ref] = reason

    if campaign is not None and campaign.test_running:
        return hold(BlockReason.TEST_RUNNING, Verdict.BORDERLINE)
    gap = timedelta(days=rules.min_change_gap_days)
    if campaign is not None and campaign.last_change_at and now - campaign.last_change_at < gap:
        return hold(BlockReason.CHANGED_RECENTLY, Verdict.BORDERLINE)
    if min(worse.clicks, better.clicks) < rules.min_clicks:
        return hold(BlockReason.LOW_DATA, Verdict.LOW_DATA)
    # Интервал худшего целиком ниже оценки лучшего, иначе «данных мало».
    if high >= rate_mean(beliefs[better.ref]):
        return hold(BlockReason.LOW_DATA, Verdict.LOW_DATA)
    if pair.p_worse_bp < rules.min_worse_probability_bp:
        return hold(BlockReason.NOT_WORSE, Verdict.BORDERLINE)
    if loss <= error_cost:
        return hold(BlockReason.WAIT_IS_CHEAPER, Verdict.BORDERLINE)
    if worse.rollback_hold:
        return hold(BlockReason.ROLLED_BACK, Verdict.INEFFICIENT)
    # Группы Директа витрина пока не знает, поэтому «не последнее объявление группы» проверяет
    # политика (этап 6) перед выполнением. Здесь оба варианта работают в одном направлении.
    plan.evaluations[worse.ref] = _evaluation(Verdict.INEFFICIENT, 0, pair.p_worse_bp)
    plan.evaluations[better.ref] = _evaluation(Verdict.EFFICIENT, pair.p_worse_bp, 0)
    plan.actions.append(
        Action(
            ActionKind.PAUSE_AD,
            worse.ref,
            worse.campaign_id,
            facts,
            rules.version,
            needs_confirmation,
        )
    )


def plan_cycle_ev(
    ads: Sequence[AdStats],
    campaigns: Sequence[CampaignStats],
    target: int,
    health: HealthReport,
    mode: Mode,
    now: datetime,
    rules: AutopilotRules,
) -> CyclePlan:
    """Детерминированный план: те же входы и правила дают тот же план."""
    if not health.trusted:
        return CyclePlan(protect_only=True)
    plan = CyclePlan(protect_only=False)
    active = [ad for ad in ads if ad.active]
    if mode is Mode.PAUSE:
        for ad in active:
            plan.evaluations[ad.ref] = _evaluation(Verdict.LOW_DATA)
        return plan
    by_id: Mapping[str, CampaignStats] = {c.campaign_id: c for c in campaigns}
    for campaign_id in sorted({ad.campaign_id for ad in active}):
        _plan_direction(
            [ad for ad in active if ad.campaign_id == campaign_id],
            by_id.get(campaign_id),
            target,
            now,
            rules,
            mode is Mode.CONFIRM,
            plan,
        )
    return plan
