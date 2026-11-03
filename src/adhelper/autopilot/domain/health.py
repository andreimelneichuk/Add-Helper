from dataclasses import dataclass
from enum import StrEnum

from adhelper.autopilot.domain.rules import AutopilotRules

BASIS_POINTS = 10000


class HealthReason(StrEnum):
    TRACKING_LOST = "tracking_lost"
    VISITS_MISMATCH = "visits_mismatch"
    CONVERSION_GAP = "conversion_gap"
    PLATFORM_INCIDENT = "platform_incident"
    MANUAL_HOLD = "manual_hold"


@dataclass(frozen=True)
class Window:
    spend: int
    leads: int
    clicks: int
    # None — визитов неоткуда взять (Метрика не подключена); проверки визитов пропускаются.
    visits: int | None


@dataclass(frozen=True)
class HealthInput:
    recent: Window
    baseline: Window
    platform_incident: bool
    manual_hold: bool


@dataclass(frozen=True)
class HealthReport:
    reasons: tuple[HealthReason, ...]

    @property
    def trusted(self) -> bool:
        return not self.reasons


def check_health(data: HealthInput, rules: AutopilotRules) -> HealthReport:
    """Плохое состояние запрещает выводы об эффективности, но не остановки ради бюджета."""
    reasons: list[HealthReason] = []
    recent = data.recent
    if data.platform_incident:
        reasons.append(HealthReason.PLATFORM_INCIDENT)
    if data.manual_hold:
        reasons.append(HealthReason.MANUAL_HOLD)
    if recent.visits is not None and recent.clicks >= rules.health_min_clicks:
        if recent.visits == 0:
            reasons.append(HealthReason.TRACKING_LOST)
        elif recent.visits * BASIS_POINTS < recent.clicks * rules.health_min_visit_share_bp:
            reasons.append(HealthReason.VISITS_MISMATCH)
    if data.baseline.spend > 0:
        expected = recent.spend * data.baseline.leads // data.baseline.spend
        if (
            expected >= rules.health_min_expected_leads
            and recent.leads * BASIS_POINTS < expected * rules.health_gap_share_bp
        ):
            reasons.append(HealthReason.CONVERSION_GAP)
    return HealthReport(tuple(reasons))
