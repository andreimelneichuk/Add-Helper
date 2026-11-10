"""Гипотеза теста из закрытого списка и только по фактам профиля"""

from adhelper.experiments.domain.model import Hypothesis, pick_hypothesis, supported_hypotheses


def test_price_needs_a_price_in_profile() -> None:
    assert Hypothesis.PRICE not in supported_hypotheses(has_prices=False, has_features=True)
    assert Hypothesis.PRICE in supported_hypotheses(has_prices=True, has_features=False)


def test_benefit_needs_a_feature_in_profile() -> None:
    assert Hypothesis.BENEFIT not in supported_hypotheses(has_prices=True, has_features=False)
    assert Hypothesis.BENEFIT in supported_hypotheses(has_prices=False, has_features=True)


def test_facts_we_do_not_store_are_never_offered() -> None:
    # Скидок, отзывов и изображений в профиле нет: такую гипотезу проверить нечем.
    offered = supported_hypotheses(has_prices=True, has_features=True)
    assert not offered & {Hypothesis.OFFER, Hypothesis.SOCIAL_PROOF, Hypothesis.IMAGE}


def test_call_to_action_needs_no_facts() -> None:
    assert supported_hypotheses(has_prices=False, has_features=False) == {Hypothesis.CALL_TO_ACTION}


def test_order_follows_closed_list_and_skips_tried() -> None:
    supported = supported_hypotheses(has_prices=True, has_features=True)
    assert pick_hypothesis(supported, set()) is Hypothesis.PRICE
    assert pick_hypothesis(supported, {"price"}) is Hypothesis.BENEFIT
    assert pick_hypothesis(supported, {"price", "benefit", "call_to_action"}) is None
