"""Правка факта в профиле: работающее объявление со старой ценой получает замену."""

from adhelper.creatives.adapters.rules_file import load_rework_rules, load_rules
from adhelper.creatives.application.rework import rework_ad
from adhelper.creatives.domain.lifecycle import (
    CreativeState,
    LiveAd,
    Replacement,
    plan_fact_updates,
)
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    CreativeSource,
    ModelUnavailable,
    Service,
)
from adhelper.creatives.domain.rework import FACT_WISH, Rework
from tests.creatives.test_generate import FakeModel, ads

RULES = load_rules("creatives-2")
REWORK = load_rework_rules("rework-1")
NEW_PRICE = BusinessProfile(
    "Лак", (Service("Маникюр", 1700),), "ул. Ленина, 5", ("Стерильные инструменты",)
)
OLD = LiveAd("lak", "1", "11", AdDraft("Маникюр 1500 ₽", "Стерильные инструменты."))
FRESH = LiveAd("lak", "1", "12", AdDraft("Маникюр на Ленина", "Стерильные инструменты."))
OTHER_OLD = LiveAd("lak", "2", "21", AdDraft("Маникюр за 1500 ₽", "Ул. Ленина, 5."))


def test_live_ad_with_old_price_gets_rewrite_others_stay() -> None:
    plan = plan_fact_updates(NEW_PRICE, [OLD, FRESH], [], frozenset(), sets_left=5)
    assert plan.rewrite == (OLD,)
    assert plan.stop == () and plan.later == ()


def test_ad_already_being_replaced_is_not_ordered_twice() -> None:
    fresh = Replacement(31, "11", CreativeState.LAUNCH_PENDING, AdDraft("Маникюр 1700 ₽", "Ок."))
    assert plan_fact_updates(NEW_PRICE, [OLD], [fresh], frozenset(), 5).rewrite == ()
    assert plan_fact_updates(NEW_PRICE, [OLD], [], frozenset({"11"}), 5).rewrite == ()


def test_outdated_replacement_is_stopped_and_ordered_again() -> None:
    # Владелец поменял цену дважды: замена с прошлой ценой тоже устарела.
    stale = Replacement(31, "11", CreativeState.NEEDS_OWNER, AdDraft("Маникюр 1600 ₽", "Ок."))
    plan = plan_fact_updates(NEW_PRICE, [OLD], [stale], frozenset(), 5)
    assert plan.stop == (31,)
    assert plan.rewrite == (OLD,)


def test_daily_limit_leaves_rest_for_later() -> None:
    plan = plan_fact_updates(NEW_PRICE, [OLD, OTHER_OLD], [], frozenset(), sets_left=1)
    assert plan.rewrite == (OLD,)
    assert plan.later == (OTHER_OLD,)


async def test_fact_update_keeps_small_edit_and_one_variant() -> None:
    # Смена цены — малая правка: почти копия старого текста здесь и нужна.
    model = FakeModel(
        ads(
            {"headline": "Маникюр 1700 ₽", "text": "Стерильные инструменты."},
            {"headline": "Маникюр 1500 ₽", "text": "Стерильные инструменты."},
            {"headline": "Маникюр на Ленина, 5", "text": "Цена 1700 ₽."},
        )
    )
    rework = Rework("11", OLD.draft, FACT_WISH, auto=True)
    result = await rework_ad(NEW_PRICE, rework, model, RULES, REWORK)
    assert [d.headline for d in result.drafts] == ["Маникюр 1700 ₽"]


async def test_fact_update_without_model_uses_template_from_profile() -> None:
    rework = Rework("11", OLD.draft, FACT_WISH, auto=True)
    for model in (None, FakeModel(ModelUnavailable())):
        result = await rework_ad(NEW_PRICE, rework, model, RULES, REWORK)
        assert result.source is CreativeSource.TEMPLATE
        assert len(result.drafts) == 1
        assert "1500" not in result.drafts[0].headline + result.drafts[0].text
