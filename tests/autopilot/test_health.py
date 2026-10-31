"""Качество данных: docs/04-autopilot.md §3."""

from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.domain.health import HealthInput, HealthReason, Window, check_health

RULES = load_rules("autopilot-1")
BASE = Window(spend=10_000_000_000, leads=50, clicks=5000, visits=4500)


def health(recent: Window, incident: bool = False, hold: bool = False) -> set[HealthReason]:
    return set(check_health(HealthInput(recent, BASE, incident, hold), RULES).reasons)


def test_normal_week_is_trusted() -> None:
    assert health(Window(2_000_000_000, 9, 1000, 900)) == set()


def test_lost_visits_mean_tracking_is_broken() -> None:
    assert HealthReason.TRACKING_LOST in health(Window(2_000_000_000, 0, 1000, 0))


def test_few_clicks_prove_nothing_about_tracking() -> None:
    assert health(Window(2_000_000, 0, 3, 0)) == set()


def test_clicks_far_from_visits_are_reported() -> None:
    assert HealthReason.VISITS_MISMATCH in health(Window(2_000_000_000, 9, 1000, 300))


def test_leads_gap_against_history_is_reported() -> None:
    assert HealthReason.CONVERSION_GAP in health(Window(2_000_000_000, 1, 1000, 900))


def test_small_expected_volume_is_not_called_a_gap() -> None:
    assert health(Window(200_000_000, 0, 100, 90)) == set()


def test_no_history_means_no_gap_verdict() -> None:
    report = check_health(
        HealthInput(Window(2_000_000_000, 0, 1000, 900), Window(0, 0, 0, 0), False, False), RULES
    )
    assert report.trusted


def test_platform_incident_and_manual_hold_block_trust() -> None:
    normal = Window(2_000_000_000, 9, 1000, 900)
    assert health(normal, incident=True) == {HealthReason.PLATFORM_INCIDENT}
    assert health(normal, hold=True) == {HealthReason.MANUAL_HOLD}
