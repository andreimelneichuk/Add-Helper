from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from adhelper.budget.domain.limits import CampaignBudget, LimitChange
from adhelper.budget.domain.ports import PlatformUnavailable
from adhelper.budget.domain.watch import SpendSnapshot


@dataclass
class Fault:
    operation: str
    after_apply: bool = False


class FakePlatform:
    """Площадка с управляемым временем, задержкой статистики и внедрением сбоев."""

    def __init__(
        self,
        campaigns: list[CampaignBudget],
        now: datetime,
        stats_lag: timedelta = timedelta(0),
        enforces_limits: bool = True,
    ) -> None:
        self.campaigns = {campaign.campaign_id: campaign for campaign in campaigns}
        self.now = now
        self.stats_lag = stats_lag
        self.enforces_limits = enforces_limits
        self.unavailable = False
        self.faults: list[Fault] = []
        self.set_limit_calls: list[LimitChange] = []
        self.limit_sums = [self.limit_sum]
        self._history = [(now, self.total_spent)]

    @property
    def limit_sum(self) -> int:
        return sum(campaign.limit for campaign in self.campaigns.values())

    @property
    def total_spent(self) -> int:
        return sum(campaign.spent for campaign in self.campaigns.values())

    def run(self, minutes: int, per_minute: int) -> None:
        """Откручивает расход поминутно; каждая активная кампания тратит per_minute."""
        for _ in range(minutes):
            self.now += timedelta(minutes=1)
            for campaign_id, campaign in self.campaigns.items():
                if not campaign.active:
                    continue
                spent = campaign.spent + per_minute
                if self.enforces_limits:
                    spent = min(spent, max(campaign.limit, campaign.spent))
                self.campaigns[campaign_id] = replace(campaign, spent=spent)
            self._history.append((self.now, self.total_spent))

    def _check_fault(self, operation: str, after_apply: bool) -> None:
        if self.unavailable:
            raise PlatformUnavailable(operation)
        for fault in self.faults:
            if fault.operation == operation and fault.after_apply == after_apply:
                self.faults.remove(fault)
                raise PlatformUnavailable(operation)

    async def read_campaigns(self, account: str) -> list[CampaignBudget]:
        self._check_fault("read_campaigns", after_apply=False)
        return list(self.campaigns.values())

    async def fetch_spend(self, account: str) -> SpendSnapshot:
        self._check_fault("fetch_spend", after_apply=False)
        visible_until = self.now - self.stats_lag
        moment, reported = self._history[0]
        for moment_candidate, value in self._history:
            if moment_candidate <= visible_until:
                moment, reported = moment_candidate, value
        return SpendSnapshot(reported=reported, data_as_of=moment)

    async def set_limit(self, account: str, change: LimitChange) -> None:
        self._check_fault("set_limit", after_apply=False)
        campaign = self.campaigns[change.campaign_id]
        raised = change.new_limit > campaign.limit
        self.campaigns[change.campaign_id] = replace(
            campaign,
            limit=change.new_limit,
            last_increase_at=self.now if raised else campaign.last_increase_at,
        )
        self.set_limit_calls.append(change)
        self.limit_sums.append(self.limit_sum)
        self._check_fault("set_limit", after_apply=True)

    async def pause_campaigns(self, account: str, campaign_ids: list[str]) -> None:
        self._check_fault("pause_campaigns", after_apply=False)
        for campaign_id in campaign_ids:
            self.campaigns[campaign_id] = replace(self.campaigns[campaign_id], active=False)
        self._check_fault("pause_campaigns", after_apply=True)
