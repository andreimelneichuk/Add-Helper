"""Набор рецензента и ворота «не пропускает рискованное». Платная модель не вызывается."""

import json
import re
from collections.abc import Sequence

from evals.meter import MAX_OUTPUT_MICRO_PER_M, Price
from evals.review_cases import CASES
from evals.review_gate import CaseRun, estimate, score

from adhelper.autopilot.adapters.reviewer import ModelReviewer


def test_forty_cases_half_risky_with_unique_ids() -> None:
    assert len(CASES) == 40
    assert len({case.id for case in CASES}) == 40
    assert len({case.candidate.ref for case in CASES}) == 40
    assert sum(1 for case in CASES if case.label == "stop") == 20
    assert {c.candidate.kind for c in CASES} == {"pause_ad", "increase_limit"}


def test_cases_carry_only_numbers() -> None:
    for case in CASES:
        assert all(isinstance(v, int) for v in case.candidate.facts.values())


class Reply:
    def __init__(self, text: str) -> None:
        self.text = text


class Scripted:
    """Поддельная модель: отвечает по правилу, ничего не стоит."""

    def __init__(self, verdict: str | None) -> None:
        self.verdict = verdict
        self.calls = 0

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> Reply:
        self.calls += 1
        if self.verdict is None:
            raise RuntimeError("down")
        refs = [c["ref"] for c in json.loads(user)["candidates"]]
        stop = {case.candidate.ref for case in CASES if case.label == "stop"}
        verdicts = [
            {
                "ref": ref,
                "verdict": self.verdict
                if self.verdict != "oracle"
                else ("doubt" if ref in stop else "ok"),
                "reason": "x",
            }
            for ref in refs
        ]
        return Reply(json.dumps({"verdicts": verdicts}))


async def run_all(verdict: str | None) -> Sequence[CaseRun]:
    reviewer = ModelReviewer(Scripted(verdict))
    runs = []
    for case in CASES:
        result = await reviewer.review([case.candidate])
        runs.append(CaseRun(case, case.candidate.ref in result.passed, result.available))
    return runs


async def test_gate_is_passed_by_a_model_that_stops_every_risky_case() -> None:
    metrics = score(await run_all("oracle"))
    assert (metrics.misses, metrics.false_stops, metrics.unavailable) == (0, 0, 0)
    assert metrics.gate_passed
    assert 0 < metrics.miss_upper < 0.2


async def test_gate_fails_when_a_risky_case_slips_through() -> None:
    metrics = score(await run_all("ok"))
    assert metrics.misses == 20
    assert not metrics.gate_passed


async def test_gate_fails_when_the_model_is_down_even_though_nothing_passed() -> None:
    metrics = score(await run_all(None))
    assert metrics.misses == 0 and metrics.unavailable == 40
    assert not metrics.gate_passed


def test_over_cautious_model_is_not_a_gate_failure_but_is_counted() -> None:
    runs = [CaseRun(case, False, True) for case in CASES]
    metrics = score(runs)
    assert metrics.gate_passed
    assert metrics.false_stops == 20


def test_worst_case_at_the_most_expensive_allowed_model_is_within_five_cents() -> None:
    price = Price(MAX_OUTPUT_MICRO_PER_M, MAX_OUTPUT_MICRO_PER_M)
    calls, worst = estimate(price)
    assert calls == 40
    assert worst <= 50_000  # $0,05, предел из CLAUDE.md


def test_refs_sent_to_the_model_do_not_leak_the_label_or_the_case_number() -> None:
    from evals.review_cases import CASE_BY_REF, CASES

    from adhelper.autopilot.domain.review import build_request

    assert len(CASE_BY_REF) == len(CASES)
    for case in CASES:
        request = build_request([case.candidate])
        assert case.id not in request
        # В `ref` нет ни метки, ни слов разметки: только вид и число.
        assert re.fullmatch(r"(ad|c):\d+", case.candidate.ref)
    # Номера не идут в порядке набора, где метки сгруппированы.
    order = [int(case.candidate.ref.split(":")[1]) for case in CASES]
    assert order != sorted(order)
