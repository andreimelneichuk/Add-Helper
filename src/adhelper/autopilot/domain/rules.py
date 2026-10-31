from dataclasses import dataclass


@dataclass(frozen=True)
class AutopilotRules:
    version: str
    maturity_days: int
    learning_days: int
    learning_target_multiple: int
    prior_weight: int
    good_probability_bp: int
    bad_probability_bp: int
    bad_cpl_multiple_bp: int
    bad_min_spend_multiple: int
    min_change_gap_days: int
    budget_increase_bp: int
    health_recent_days: int
    health_min_clicks: int
    health_min_visit_share_bp: int
    health_min_expected_leads: int
    health_gap_share_bp: int
