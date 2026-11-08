from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from adhelper.budget.domain.limits import CampaignBudget, LimitChange, LimitDecision
from adhelper.budget.domain.ports import PlatformUnavailable
from adhelper.budget.domain.watch import SpendSnapshot
from adhelper.campaigns.domain.launch import NewCampaign
from adhelper.campaigns.domain.ports import CreatedCampaign, Targeting
from adhelper.campaigns.domain.targeting import Schedule
from adhelper.platforms.domain.ports import (
    AdDay,
    AdImage,
    Advertiser,
    ModerationState,
    ModerationVerdict,
    PublishedAd,
)

# Новая кампания в Директе показывается круглосуточно; регион задаём Москвой.
DEFAULT_TARGETING = Targeting(Schedule((0, 1, 2, 3, 4, 5, 6), 0, 24), (213,))


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
        balance: int = 10**15,
    ) -> None:
        self.campaigns = {campaign.campaign_id: campaign for campaign in campaigns}
        self.now = now
        self.stats_lag = stats_lag
        self.enforces_limits = enforces_limits
        self.balance = balance
        self.faults_for_balance = False
        self.unavailable = False
        self.stats_frozen_at: datetime | None = None
        self.faults: list[Fault] = []
        self.ads: dict[str, bool] = {}
        # Объявление → его группа на площадке.
        self.groups: dict[str, str] = {}
        # Отчёт по объявлениям и запросы к нему: (кабинет, кампании, с, по).
        self.ad_report: list[AdDay] = []
        self.ad_report_calls: list[tuple[str, tuple[str, ...], date, date]] = []
        # Только изменённые; остальные кампании показываются по DEFAULT_TARGETING.
        self.targeting: dict[str, Targeting] = {}
        self.schedule_calls = 0
        self.set_limit_calls: list[LimitChange] = []
        self.rejections: list[LimitDecision] = []
        self.limit_sums = [self.limit_sum]
        # Кампании запуска по нашей метке; номера выдаёт площадка.
        self.created: dict[str, CreatedCampaign] = {}
        self.create_calls = 0
        # Объявления, созданные по нашей метке, и итоги проверки; без итога — «на проверке».
        self.published: dict[str, PublishedAd] = {}
        self.create_ad_calls = 0
        # Библиотека картинок: одинаковая картинка хранится один раз, как в Директе.
        self.images: dict[str, AdImage] = {}
        self.upload_calls = 0
        self.verdicts: dict[str, ModerationVerdict] = {}
        self.advertisers: dict[str, Advertiser] = {}
        self.set_advertiser_calls = 0
        # Визиты с рекламы по московским дням; дня нет — у Метрики нет данных за него.
        self.visits: dict[date, int] = {}
        self.visit_calls: list[tuple[str, date, date]] = []
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
                spent = campaign.spent + min(per_minute, self.balance)
                if self.enforces_limits:
                    spent = min(spent, max(campaign.limit, campaign.spent))
                self.balance -= spent - campaign.spent
                self.campaigns[campaign_id] = replace(campaign, spent=spent)
            self._history.append((self.now, self.total_spent))

    def _check_fault(self, operation: str, after_apply: bool) -> None:
        if self.unavailable:
            raise PlatformUnavailable(operation)
        for fault in self.faults:
            if fault.operation == operation and fault.after_apply == after_apply:
                self.faults.remove(fault)
                raise PlatformUnavailable(operation)

    async def read_ad(self, account: str, ad_ref: str) -> dict[str, bool]:
        self._check_fault("read_ad", after_apply=False)
        return {"active": self.ads[ad_ref]} if ad_ref in self.ads else {}

    async def set_ad_active(self, account: str, ad_ref: str, active: bool) -> None:
        self._check_fault("set_ad_active", after_apply=False)
        self.ads[ad_ref] = active

    async def read_targeting(self, account: str, campaign_id: str) -> Targeting | None:
        self._check_fault("read_targeting", after_apply=False)
        if campaign_id not in self.campaigns:
            return None
        return self.targeting.get(campaign_id, DEFAULT_TARGETING)

    async def set_schedule(self, account: str, campaign_id: str, schedule: Schedule) -> None:
        self._check_fault("set_schedule", after_apply=False)
        current = self.targeting.get(campaign_id, DEFAULT_TARGETING)
        self.targeting[campaign_id] = replace(current, schedule=schedule)
        self.schedule_calls += 1
        self._check_fault("set_schedule", after_apply=True)

    async def set_regions(self, account: str, campaign_id: str, regions: tuple[int, ...]) -> None:
        self._check_fault("set_regions", after_apply=False)
        current = self.targeting.get(campaign_id, DEFAULT_TARGETING)
        self.targeting[campaign_id] = replace(current, regions=regions)
        self._check_fault("set_regions", after_apply=True)

    async def find_campaign(self, account: str, marker: str) -> CreatedCampaign | None:
        self._check_fault("find_campaign", after_apply=False)
        return self.created.get(marker)

    async def create_campaign(self, account: str, campaign: NewCampaign) -> None:
        self._check_fault("create_campaign", after_apply=False)
        self.create_calls += 1
        campaign_id = str(900 + len(self.created) + 1)
        ad_ids = tuple(f"{campaign_id}0{i + 1}" for i in range(len(campaign.ads)))
        self.created[campaign.marker] = CreatedCampaign(campaign_id, campaign, ad_ids)
        # Новая кампания тратит в пределах своего лимита, как остальные.
        self.campaigns[campaign_id] = CampaignBudget(campaign_id, campaign.limit, 0, True, None)
        for ad_id in ad_ids:
            self.ads[f"ad:{ad_id}"] = True
        self.limit_sums.append(self.limit_sum)
        self._check_fault("create_campaign", after_apply=True)

    async def find_ad(self, account: str, marker: str) -> PublishedAd | None:
        self._check_fault("find_ad", after_apply=False)
        return self.published.get(marker)

    async def create_ad(
        self,
        account: str,
        campaign_id: str,
        marker: str,
        headline: str,
        text: str,
        href: str,
        group_id: str | None = None,
        image_hash: str | None = None,
    ) -> None:
        self._check_fault("create_ad", after_apply=False)
        if image_hash is not None and image_hash not in self.images:
            raise ValueError(f"нет картинки {image_hash}")
        if campaign_id not in self.campaigns:
            raise ValueError(f"нет кампании {campaign_id}")
        self.create_ad_calls += 1
        ad_id = str(7000 + len(self.published) + 1)
        group = group_id or f"g-{campaign_id}"
        self.published[marker] = PublishedAd(
            ad_id, campaign_id, headline, text, href, image_hash=image_hash, group_id=group
        )
        self.groups[ad_id] = group
        # Новое объявление включено, но показывается только после одобрения.
        self.ads[f"ad:{ad_id}"] = True
        self._check_fault("create_ad", after_apply=True)

    async def upload_image(self, account: str, image: AdImage) -> str:
        self._check_fault("upload_image", after_apply=False)
        self.upload_calls += 1
        image_hash = f"h{image.sha256[:19]}"
        self.images[image_hash] = image
        self._check_fault("upload_image", after_apply=True)
        return image_hash

    async def ad_group(self, account: str, ad_id: str) -> str | None:
        self._check_fault("ad_group", after_apply=False)
        return self.groups.get(ad_id)

    async def moderation(self, account: str, ad_ids: Sequence[str]) -> list[ModerationVerdict]:
        self._check_fault("moderation", after_apply=False)
        waiting = ModerationState.MODERATION
        return [self.verdicts.get(ad_id, ModerationVerdict(ad_id, waiting)) for ad_id in ad_ids]

    async def read_advertiser(self, account: str) -> Advertiser | None:
        self._check_fault("read_advertiser", after_apply=False)
        return self.advertisers.get(account)

    async def set_advertiser(self, account: str, advertiser: Advertiser) -> None:
        self._check_fault("set_advertiser", after_apply=False)
        self.set_advertiser_calls += 1
        self.advertisers[account] = advertiser
        self._check_fault("set_advertiser", after_apply=True)

    def accept(self, ad_id: str) -> None:
        self.verdicts[ad_id] = ModerationVerdict(ad_id, ModerationState.ACCEPTED)

    def reject(self, ad_id: str, *reasons: str) -> None:
        self.verdicts[ad_id] = ModerationVerdict(ad_id, ModerationState.REJECTED, reasons)

    async def read_campaigns(self, account: str) -> list[CampaignBudget]:
        self._check_fault("read_campaigns", after_apply=False)
        return list(self.campaigns.values())

    async def fetch_spend(self, account: str) -> SpendSnapshot:
        self._check_fault("fetch_spend", after_apply=False)
        visible_until = self.now - self.stats_lag
        if self.stats_frozen_at is not None:
            visible_until = min(visible_until, self.stats_frozen_at)
        moment, reported = self._history[0]
        for moment_candidate, value in self._history:
            if moment_candidate <= visible_until:
                moment, reported = moment_candidate, value
        return SpendSnapshot(reported=reported, data_as_of=moment)

    async def fetch_balance(self, account: str) -> int:
        self._check_fault("fetch_balance", after_apply=False)
        if self.faults_for_balance:
            raise PlatformUnavailable("fetch_balance")
        return self.balance

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

    async def resume_campaigns(self, account: str, campaign_ids: list[str]) -> None:
        self._check_fault("resume_campaigns", after_apply=False)
        for campaign_id in campaign_ids:
            self.campaigns[campaign_id] = replace(self.campaigns[campaign_id], active=True)
        self._check_fault("resume_campaigns", after_apply=True)

    async def archive_campaigns(self, account: str, campaign_ids: list[str]) -> None:
        for campaign_id in campaign_ids:
            if campaign_id in self.campaigns:
                self.campaigns[campaign_id] = replace(self.campaigns[campaign_id], active=False)

    async def record_rejection(
        self, account: str, change: LimitChange, decision: LimitDecision
    ) -> None:
        self.rejections.append(decision)

    async def ad_days(
        self, account: str, campaign_ids: Sequence[str], since: date, until: date
    ) -> list[AdDay]:
        self._check_fault("ad_days", after_apply=False)
        self.ad_report_calls.append((account, tuple(campaign_ids), since, until))
        return [row for row in self.ad_report if since <= row.day <= until]

    async def ad_visits(self, counter: str, since: date, until: date) -> int | None:
        self.visit_calls.append((counter, since, until))
        if self.unavailable:
            raise PlatformUnavailable("metrika")
        self._check_fault("ad_visits", after_apply=False)
        days = [day for day in self.visits if since <= day <= until]
        if not days:
            return None
        return sum(self.visits[day] for day in days)
