"""Учёт денег платных прогонов evals (docs/13-model-spend.md). Сеть не нужна."""

import asyncio

import pytest
from evals import gate
from evals.meter import BudgetSpent, Meter, Price, parse_prices, worst_case_micro

from adhelper.creatives.adapters.openai_compatible import Usage
from adhelper.creatives.domain.model import ModelReply, ModelUnavailable

PRICE = Price(input_micro_per_m=50_000, output_micro_per_m=250_000)  # $0.05 и $0.25 за 1M


def test_cost_from_provider_is_preferred() -> None:
    meter = Meter(budget_micro=10_000)
    meter.record(PRICE, Usage(1000, 1000, 0.000123))
    assert meter.spent_micro == 123


def test_cost_from_price_rounds_up() -> None:
    meter = Meter(budget_micro=10_000)
    meter.record(PRICE, Usage(1, 1, None))
    # 0.05 + 0.25 микродоллара — округляем вверх, чтобы не занизить расход.
    assert meter.spent_micro == 1
    meter.record(PRICE, Usage(1_000_000, 1_000_000, None))
    assert meter.spent_micro == 300_001


def test_budget_stops_further_calls() -> None:
    meter = Meter(budget_micro=100)
    meter.check()
    meter.record(PRICE, Usage(0, 0, 0.0001))
    with pytest.raises(BudgetSpent):
        meter.check()


def test_worst_case_uses_max_tokens() -> None:
    # 10 вызовов по 2000 входных и 1000 выходных: 10 × (100 + 250) микродолларов.
    assert worst_case_micro(PRICE, 10, 2000, 1000) == 3500


def test_prices_from_openrouter_catalog() -> None:
    payload = {
        "data": [
            {"id": "a/x", "pricing": {"prompt": "0.00000005", "completion": "0.00000025"}},
            {"id": "b/y", "pricing": {"prompt": "0", "completion": "0"}},
        ]
    }
    assert parse_prices(payload, ["a/x"]) == {"a/x": PRICE}
    with pytest.raises(SystemExit):
        parse_prices(payload, ["c/z"])


class Scripted:
    def __init__(self, *steps: object) -> None:
        self.steps = list(steps)
        self.calls = 0

    async def complete_json(self, *_: object) -> ModelReply:
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, str)
        return ModelReply(step, 10)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr("evals.gate.asyncio.sleep", instant)


def endpoint(model: Scripted, meter: Meter) -> gate._Endpoint:
    return gate._Endpoint(model, False, asyncio.Semaphore(1), meter)


async def test_paid_failures_are_retried_only_twice() -> None:
    model = Scripted(*["не json"] * 8)
    assert await gate._call(endpoint(model, Meter(10_000)), "s", "u", {}, 10) is None
    assert model.calls == 1 + gate.PAID_RETRIES


async def test_free_failures_keep_retrying() -> None:
    model = Scripted(ModelUnavailable("HTTPStatusError"), ModelUnavailable("x"), "{}")
    assert await gate._call(endpoint(model, Meter(10_000)), "s", "u", {}, 10) == "{}"


async def test_cut_reply_counts_as_paid() -> None:
    model = Scripted(*[ModelUnavailable("length", tokens=50)] * 8)
    assert await gate._call(endpoint(model, Meter(10_000)), "s", "u", {}, 10) is None
    assert model.calls == 1 + gate.PAID_RETRIES


async def test_spent_budget_stops_without_calling() -> None:
    meter = Meter(budget_micro=0)
    model = Scripted("{}")
    assert await gate._call(endpoint(model, meter), "s", "u", {}, 10) is None
    assert model.calls == 0
    assert meter.stopped


def test_model_above_price_limit_is_refused() -> None:
    payload = {
        "data": [{"id": "a/x", "pricing": {"prompt": "0.00000015", "completion": "0.000002"}}]
    }
    with pytest.raises(SystemExit, match="дороже"):
        parse_prices(payload, ["a/x"])
