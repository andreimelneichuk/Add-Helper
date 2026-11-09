"""Политика автопилота: жёсткие рамки, внутри которых действие вообще допустимо.

Политика стоит между решением правил и исполнителем и не зависит от того, кто решил: правила
или модель-рецензент. Неизвестное состояние объекта трактуем в худшую для действия сторону.
Все пороги живут в файле `policy-N.toml`, каждое решение несёт версию и входные данные.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

BASIS_POINTS = 10_000


class PolicyAction(StrEnum):
    PAUSE_AD = "pause_ad"
    INCREASE_LIMIT = "increase_limit"
    TRANSFER_LIMIT = "transfer_limit"
    START_TEST = "start_test"
    # Пауза кампании запрещена всегда: кампанию останавливает только владелец или страж бюджета.
    PAUSE_CAMPAIGN = "pause_campaign"


# Перенос лимита поднимает лимит получателя, поэтому разрешён тем же классом, что и рост.
_PERMISSION_CLASS = {PolicyAction.TRANSFER_LIMIT: PolicyAction.INCREASE_LIMIT}
_MONEY = frozenset({PolicyAction.INCREASE_LIMIT, PolicyAction.TRANSFER_LIMIT})


class PolicyReason(StrEnum):
    CLASS_NOT_ALLOWED = "class_not_allowed"
    CAMPAIGN_PAUSE_FORBIDDEN = "campaign_pause_forbidden"
    DATA_ALERT = "data_alert"
    API_POINTS_LOW = "api_points_low"
    DAILY_LIMIT = "daily_limit"
    LAST_AD_IN_GROUP = "last_ad_in_group"
    IN_LEARNING = "in_learning"
    AUTO_STRATEGY_INTERVAL = "auto_strategy_interval"
    WEEKLY_SHARE = "weekly_share"
    BAD_AMOUNT = "bad_amount"


@dataclass(frozen=True)
class PolicyRules:
    version: str
    max_changes_per_day: int
    # Доля рабочего бюджета, сдвигаемая за 7 дней ростом и переносом (базисные пункты).
    max_weekly_move_bp: int
    # Интервал между изменениями лимита кампании с автостратегией: чаще сбиваем обучение.
    auto_strategy_interval_days: int
    # Меньше активных объявлений в группе паузе не подлежит: останется хотя бы одно.
    min_group_active_ads: int
    ad_learning_days: int
    # Меньше баллов API: контроль бюджета важнее оптимизации, оптимизацию откладываем.
    min_api_points: int

    def __post_init__(self) -> None:
        problems = []
        if self.max_changes_per_day < 1:
            problems.append("max_changes_per_day не меньше 1")
        if not 0 <= self.max_weekly_move_bp <= BASIS_POINTS:
            problems.append("max_weekly_move_bp вне 0..10000")
        if self.auto_strategy_interval_days < 0:
            problems.append("auto_strategy_interval_days не может быть отрицательным")
        if self.min_group_active_ads < 2:
            problems.append("min_group_active_ads не меньше 2")
        if self.ad_learning_days < 0 or self.min_api_points < 0:
            problems.append("пороги не могут быть отрицательными")
        if problems:
            raise ValueError(f"Правила {self.version}: " + "; ".join(problems))


@dataclass(frozen=True)
class PolicyInput:
    action: PolicyAction
    ref: str
    campaign_id: str
    # Классы, разрешённые настройками автопилота этого бизнеса.
    allowed: frozenset[PolicyAction]
    now: datetime
    changes_today: int
    # Сколько уже сдвинуто ростом и переносом лимитов за 7 дней, микрорубли.
    moved_this_week: int
    # Сумма действия в микрорублях: рост или перенос; для остальных действий 0.
    amount: int
    operating_budget: int
    # None: состав группы неизвестен. Трактуем как последнее объявление.
    active_ads_in_group: int | None
    ad_in_learning: bool
    # Возраст объявления в днях; моложе `ad_learning_days` значит обучение. None: возраст не знаем,
    # тогда решает только `ad_in_learning`.
    campaign_in_learning: bool | None
    # None: неизвестно. Трактуем как автостратегию.
    auto_strategy: bool | None
    # Последнее изменение лимита кампании, рост или снижение. None: данных нет, а не «не менялся».
    last_limit_change_at: datetime | None
    data_alert: bool
    # None: баллы не читали, тогда не блокируем: бюджет охраняет страж, а не политика.
    api_points_rest: int | None
    ad_age_days: int | None = None


@dataclass(frozen=True)
class PolicyDecision:
    reason: PolicyReason | None
    rules_version: str
    inputs: dict[str, Any]

    @property
    def allowed(self) -> bool:
        return self.reason is None


def _inputs(inp: PolicyInput) -> dict[str, Any]:
    def number(value: bool | int | None) -> int:
        return -1 if value is None else int(value)

    return {
        "action": inp.action.value,
        "changes_today": inp.changes_today,
        "moved_this_week_micros": inp.moved_this_week,
        "amount_micros": inp.amount,
        "operating_budget_micros": inp.operating_budget,
        "active_ads_in_group": number(inp.active_ads_in_group),
        "ad_in_learning": int(inp.ad_in_learning),
        "campaign_in_learning": number(inp.campaign_in_learning),
        "auto_strategy": number(inp.auto_strategy),
        "last_limit_change_at": inp.last_limit_change_at.isoformat()
        if inp.last_limit_change_at
        else "",
        "data_alert": int(inp.data_alert),
        "api_points_rest": number(inp.api_points_rest),
        "ad_age_days": number(inp.ad_age_days),
    }


def check_policy(inp: PolicyInput, rules: PolicyRules) -> PolicyDecision:
    """Первая сработавшая причина отказа. Порядок: самое жёсткое выше."""
    return PolicyDecision(_reason(inp, rules), rules.version, _inputs(inp))


def _reason(inp: PolicyInput, rules: PolicyRules) -> PolicyReason | None:
    action = inp.action
    if action is PolicyAction.PAUSE_CAMPAIGN:
        return PolicyReason.CAMPAIGN_PAUSE_FORBIDDEN
    if _PERMISSION_CLASS.get(action, action) not in inp.allowed:
        return PolicyReason.CLASS_NOT_ALLOWED
    if inp.data_alert:
        return PolicyReason.DATA_ALERT
    if inp.api_points_rest is not None and inp.api_points_rest < rules.min_api_points:
        return PolicyReason.API_POINTS_LOW
    if inp.changes_today >= rules.max_changes_per_day:
        return PolicyReason.DAILY_LIMIT
    if action is PolicyAction.PAUSE_AD:
        if inp.active_ads_in_group is None or inp.active_ads_in_group < rules.min_group_active_ads:
            return PolicyReason.LAST_AD_IN_GROUP
        if inp.ad_in_learning or (
            inp.ad_age_days is not None and inp.ad_age_days < rules.ad_learning_days
        ):
            return PolicyReason.IN_LEARNING
        # Обучение кампании паузе объявления мешает только если известно точно.
        if inp.campaign_in_learning:
            return PolicyReason.IN_LEARNING
    if action in _MONEY:
        return _money_reason(inp, rules)
    return None


def _money_reason(inp: PolicyInput, rules: PolicyRules) -> PolicyReason | None:
    if inp.amount <= 0:
        return PolicyReason.BAD_AMOUNT
    if inp.campaign_in_learning is None or inp.campaign_in_learning:
        return PolicyReason.IN_LEARNING
    if inp.auto_strategy is None or inp.auto_strategy:
        # Нет сведений о последнем изменении: не отличаем «не менялся» от «не знаем», отказываем.
        wait = timedelta(days=rules.auto_strategy_interval_days)
        if inp.last_limit_change_at is None or inp.now - inp.last_limit_change_at < wait:
            return PolicyReason.AUTO_STRATEGY_INTERVAL
    cap = inp.operating_budget * rules.max_weekly_move_bp // BASIS_POINTS
    if inp.moved_this_week + inp.amount > cap:
        return PolicyReason.WEEKLY_SHARE
    return None
