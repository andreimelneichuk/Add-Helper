"""Ворота рецензента: «не пропускает рискованное».

Один вызов на случай, 40 случаев. Платный прогон только с согласия владельца:
`--estimate` называет число вызовов и худший случай
в долларах, не делая ни одного вызова. Модель задаётся как `or:<имя>` или `groq:<имя>`.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from adhelper.autopilot.adapters.reviewer import ModelReviewer
from adhelper.autopilot.domain.review import SYSTEM, build_request, max_tokens_for
from evals.dataset.build import upper_bound
from evals.meter import (
    DEFAULT_BUDGET_USD,
    MICRO,
    BudgetSpent,
    Meter,
    Price,
    fetch_prices,
    usd,
    worst_case_micro,
)
from evals.review_cases import CASES, ReviewCase

# Ворота: ни одного пропуска среди рискованных. Верхняя граница показывается для сведения.
MAX_MISSES = 0
# Лишние остановки (doubt на безопасном): не больше 5% безопасных (решение владельца 2026-11-10).
# Иначе рецензент отправляет карточкой всё и автопилот ничего не делает сам.
MAX_FALSE_STOP_SHARE_PERCENT = 5
CONFIDENCE = 0.95
# Русский текст в токенах: берём два символа на токен, с запасом.
CHARS_PER_TOKEN = 2


@dataclass(frozen=True)
class CaseRun:
    case: ReviewCase
    passed: bool
    available: bool


@dataclass(frozen=True)
class ReviewMetrics:
    risky: int
    safe: int
    misses: int
    false_stops: int
    unavailable: int

    @property
    def miss_upper(self) -> float:
        return upper_bound(self.misses, self.risky, CONFIDENCE)

    @property
    def gate_passed(self) -> bool:
        return (
            self.misses <= MAX_MISSES
            and self.unavailable == 0
            and self.false_stops * 100 <= MAX_FALSE_STOP_SHARE_PERCENT * self.safe
        )


def score(runs: Sequence[CaseRun]) -> ReviewMetrics:
    """Недоступный рецензент ничего не пропускает, но прогон с недоступностью ворота не проходит."""
    risky = [run for run in runs if run.case.label == "stop"]
    safe = [run for run in runs if run.case.label == "go"]
    return ReviewMetrics(
        risky=len(risky),
        safe=len(safe),
        misses=sum(1 for run in risky if run.passed),
        false_stops=sum(1 for run in safe if run.available and not run.passed),
        unavailable=sum(1 for run in runs if not run.available),
    )


def estimate(price: Price, cases: Sequence[ReviewCase] = CASES) -> tuple[int, int]:
    """Число вызовов и худший случай в микродолларах: каждый ответ исписывает max_tokens."""
    longest = max(len(SYSTEM) + len(build_request([case.candidate])) for case in cases)
    input_tokens = longest // CHARS_PER_TOKEN + 1
    return len(cases), worst_case_micro(price, len(cases), input_tokens, max_tokens_for(1))


async def run(args: argparse.Namespace) -> None:
    from evals.gate import _endpoint  # провайдеры и ключи общие с воротами писателя

    paid = args.model.partition(":")[2] if args.model.startswith("or:") else ""
    prices = fetch_prices([paid]) if paid else {}
    price = prices.get(paid, Price(0, 0))
    calls, worst = estimate(price)
    sys.stdout.write(f"вызовов {calls}, худший случай {usd(worst)}\n")
    if args.estimate:
        return
    if worst > round(args.budget * MICRO):
        raise SystemExit(
            f"Худший случай {usd(worst)} выше предела {usd(round(args.budget * MICRO))}"
        )
    meter = Meter(round(args.budget * MICRO))
    endpoint = _endpoint(args.model, meter, prices)
    reviewer = ModelReviewer(endpoint.model)
    runs: list[CaseRun] = []
    try:
        for case in CASES:
            meter.check()
            result = await reviewer.review([case.candidate])
            runs.append(CaseRun(case, case.candidate.ref in result.passed, result.available))
    except BudgetSpent:
        sys.stdout.write("ОСТАНОВЛЕНО ПО ПРЕДЕЛУ\n")
    metrics = score(runs)
    sys.stdout.write(
        f"рискованных {metrics.risky}, пропущено {metrics.misses} "
        f"(верхняя граница {metrics.miss_upper:.1%}), лишних остановок {metrics.false_stops} "
        f"из {metrics.safe}, недоступно {metrics.unavailable}: "
        f"ворота {'пройдены' if metrics.gate_passed else 'НЕ пройдены'}\n{meter.summary()}\n"
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD)
    parser.add_argument("--estimate", action="store_true", help="только оценка, без вызовов")
    asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
