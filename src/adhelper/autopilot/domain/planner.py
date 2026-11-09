"""Выбор движка решений по версии правил: прежний по цене обращения или по ожидаемой потере."""

from collections.abc import Sequence
from datetime import datetime

from adhelper.autopilot.domain.decide import CyclePlan, Mode, plan_cycle
from adhelper.autopilot.domain.health import HealthReport
from adhelper.autopilot.domain.model import AdStats, CampaignStats
from adhelper.autopilot.domain.plan_ev import plan_cycle_ev
from adhelper.autopilot.domain.rules import AutopilotRules

EXPECTED_LOSS = "expected_loss"


def plan_by_rules(
    ads: Sequence[AdStats],
    campaigns: Sequence[CampaignStats],
    target: int,
    health: HealthReport,
    mode: Mode,
    now: datetime,
    rules: AutopilotRules,
) -> CyclePlan:
    engine = plan_cycle_ev if rules.engine == EXPECTED_LOSS else plan_cycle
    return engine(ads, campaigns, target, health, mode, now, rules)
