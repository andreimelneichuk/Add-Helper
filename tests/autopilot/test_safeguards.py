"""Предохранители автопилота: замороженные данные, лимит откатов, аномалия исходов."""

from adhelper.autopilot.domain.outcomes import OutcomeRules, OutcomeVerdict
from adhelper.autopilot.domain.safeguards import SafeguardState, check_safeguards

RULES = OutcomeRules(
    version="outcomes-1",
    window_days=7,
    min_clicks=30,
    min_leads=5,
    worse_threshold_bp=2_000,
    better_threshold_bp=2_000,
    noise_sigmas_x10=20,
    zero_leads_spend_multiple=3,
    max_rollbacks=2,
    rollback_window_days=14,
    anomaly_window=5,
    anomaly_worse_count=3,
)
W, S, B, N = (
    OutcomeVerdict.WORSE,
    OutcomeVerdict.SAME,
    OutcomeVerdict.BETTER,
    OutcomeVerdict.INSUFFICIENT,
)


def state(
    rollbacks: int = 0, verdicts: tuple[OutcomeVerdict, ...] = (), untrusted: bool = False
) -> SafeguardState:
    return SafeguardState(
        rollbacks_in_window=rollbacks, recent_verdicts=verdicts, data_untrusted=untrusted
    )


def test_calm_state_allows_rollback() -> None:
    result = check_safeguards(state(verdicts=(S, B, W)), RULES)
    assert result.allow_rollback and result.demote_reason is None and not result.frozen


def test_untrusted_data_freezes_everything() -> None:
    result = check_safeguards(state(untrusted=True), RULES)
    assert result.frozen and not result.allow_rollback and result.demote_reason is None


def test_rollback_limit_stops_more_rollbacks_and_demotes() -> None:
    result = check_safeguards(state(rollbacks=2), RULES)
    assert not result.allow_rollback
    assert result.demote_reason is not None


def test_the_last_allowed_rollback_is_done_and_demotes() -> None:
    result = check_safeguards(state(rollbacks=1), RULES)
    assert result.allow_rollback and result.demote_reason is not None


def test_first_rollback_is_calm() -> None:
    result = check_safeguards(state(rollbacks=0), RULES)
    assert result.allow_rollback and result.demote_reason is None


def test_anomaly_three_worse_of_last_five_demotes() -> None:
    result = check_safeguards(state(verdicts=(W, S, W, B, W)), RULES)
    assert result.demote_reason is not None
    assert result.allow_rollback


def test_insufficient_outcomes_do_not_count_toward_anomaly() -> None:
    result = check_safeguards(state(verdicts=(W, N, N, W, N, N, N)), RULES)
    assert result.demote_reason is None


def test_old_worse_outside_window_do_not_count() -> None:
    result = check_safeguards(state(verdicts=(S, S, S, S, S, W, W, W)), RULES)
    assert result.demote_reason is None
