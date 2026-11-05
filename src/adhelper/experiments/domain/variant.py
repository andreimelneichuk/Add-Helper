"""Вариант для A/B-теста: один параметр меняется по гипотезе, факты только из профиля.

Пишет код из полей профиля, как запасной набор хранителя (`template_drafts`): каждый вариант
проходит ту же проверку фактов. Вариант от писателя по гипотезе появится вместе с T6.
"""

from adhelper.creatives.domain.checks import check_draft, similarity_bp, template_drafts
from adhelper.creatives.domain.model import AdDraft, BusinessProfile, CreativeRules
from adhelper.experiments.domain.model import Hypothesis


def _fits(draft: AdDraft, hypothesis: Hypothesis, profile: BusinessProfile) -> bool:
    body = f"{draft.headline} {draft.text}"
    if hypothesis is Hypothesis.PRICE:
        return any(s.price is not None and f"{s.price} ₽" in body for s in profile.services)
    if hypothesis is Hypothesis.BENEFIT:
        return any(feature in body for feature in profile.features)
    if hypothesis is Hypothesis.CALL_TO_ACTION:
        return "Запишитесь" in body
    return False


def _candidates(profile: BusinessProfile, rules: CreativeRules) -> list[AdDraft]:
    drafts = list(template_drafts(profile, rules))
    for service in profile.services:
        # Призыв без адреса и цены: меняется только призыв.
        drafts.append(AdDraft(f"{service.name} в «{profile.name}»", "Запишитесь онлайн сегодня."))
    return drafts


def variant_draft(
    profile: BusinessProfile, control: AdDraft, hypothesis: Hypothesis, rules: CreativeRules
) -> AdDraft | None:
    """Первый вариант, который отвечает гипотезе, проходит проверку и не копирует контроль."""
    for draft in _candidates(profile, rules):
        if check_draft(draft, profile, rules):
            continue
        if similarity_bp(draft, control) > rules.similarity_max_bp:
            continue
        if _fits(draft, hypothesis, profile) and not _fits(control, hypothesis, profile):
            return draft
    return None
