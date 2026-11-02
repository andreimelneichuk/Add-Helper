"""Шаг «Объявления» мастера: черновик профиля до входа и выбор владельца после входа."""

from collections.abc import Sequence
from enum import StrEnum

from adhelper.creatives.application.service import profile_from
from adhelper.creatives.domain.model import BusinessProfile, CreativeSource, SetStatus, WizardRules
from adhelper.creatives.domain.wizard import WizardDraft, WizardSet, kept_is_valid

__all__ = [
    "CURRENT_WIZARD_RULES",
    "CreativeSource",
    "DraftProblem",
    "SetStatus",
    "WizardDraft",
    "WizardSet",
    "draft_profile",
    "kept_is_valid",
]

CURRENT_WIZARD_RULES = "wizard-1"


class DraftProblem(StrEnum):
    NO_SERVICES = "no_services"
    TOO_MANY = "too_many"
    TOO_LONG = "too_long"


def draft_profile(
    niche: str,
    address: str,
    services: Sequence[tuple[str, int | None]],
    features: Sequence[str],
    rules: WizardRules,
) -> BusinessProfile | DraftProblem:
    """Факты, которые владелец подтвердил в мастере. Только они попадут в объявления."""
    if len(services) > rules.max_services or len(features) > rules.max_features:
        return DraftProblem.TOO_MANY
    texts = [niche, address, *(name for name, _ in services), *features]
    if any(len(value) > rules.max_fact_chars for value in texts):
        return DraftProblem.TOO_LONG
    profile = profile_from("", services, address, features, niche)
    if not profile.services:
        return DraftProblem.NO_SERVICES
    return profile
