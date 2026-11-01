"""Подсчёт метрик судьи. Чистые функции: без сети, чтобы их проверяли тесты."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from evals.cases import Case


@dataclass(frozen=True)
class CaseRun:
    case: Case
    votes: tuple[bool, ...]  # True — судья снял объявление, по одному на повтор
    code_blocked: bool
    reasons: tuple[str, ...] = ()

    @property
    def majority(self) -> bool:
        return sum(self.votes) * 2 > len(self.votes)

    @property
    def unstable(self) -> bool:
        return len(set(self.votes)) > 1


@dataclass(frozen=True)
class Metrics:
    violations: int
    clean: int
    caught: int
    false_blocks: int
    missed_ids: tuple[str, ...] = field(default=())
    false_block_ids: tuple[str, ...] = field(default=())

    @property
    def recall_pct(self) -> float:
        return 100 * self.caught / self.violations if self.violations else 0.0

    @property
    def false_block_pct(self) -> float:
        return 100 * self.false_blocks / self.clean if self.clean else 0.0


def metrics(runs: Sequence[CaseRun], verdict: str) -> Metrics:
    """verdict: `single:<i>` — i-й одиночный вызов, как в работе сервиса;
    `majority` — большинство повторов; `code` — только код; `combined` — код или судья."""
    scored = [run for run in runs if run.case.label != "ambiguous"]
    caught = false_blocks = 0
    missed: list[str] = []
    wrongly: list[str] = []
    for run in scored:
        blocked = _blocked(run, verdict)
        if run.case.label == "block":
            if blocked:
                caught += 1
            else:
                missed.append(run.case.id)
        elif blocked:
            false_blocks += 1
            wrongly.append(run.case.id)
    violations = sum(1 for run in scored if run.case.label == "block")
    return Metrics(
        violations, len(scored) - violations, caught, false_blocks, tuple(missed), tuple(wrongly)
    )


def _blocked(run: CaseRun, verdict: str) -> bool:
    if verdict == "code":
        return run.code_blocked
    if verdict == "majority":
        return run.majority
    if verdict == "combined":
        return run.code_blocked or run.votes[0]
    if verdict == "combined_majority":
        return run.code_blocked or run.majority
    if verdict.startswith("single:"):
        return run.votes[int(verdict.split(":")[1])]
    raise ValueError(verdict)


def single_call_average(runs: Sequence[CaseRun]) -> tuple[float, float]:
    """Средние полнота и доля ложных снятий по повторам одиночного вызова."""
    repeats = min(len(run.votes) for run in runs)
    results = [metrics(runs, f"single:{i}") for i in range(repeats)]
    recall = sum(m.recall_pct for m in results) / repeats
    false_block = sum(m.false_block_pct for m in results) / repeats
    return recall, false_block


def misses_by_category(runs: Sequence[CaseRun], verdict: str) -> Counter[str]:
    by_id = {run.case.id: run.case.category for run in runs}
    return Counter(by_id[case_id] for case_id in metrics(runs, verdict).missed_ids)
