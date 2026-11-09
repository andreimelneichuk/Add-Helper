"""Ворота перед самостоятельным действием: политика, затем рецензент; отказ = карточка."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from adhelper.autopilot.adapters.rules_file import load_policy_rules, load_rules
from adhelper.autopilot.application.cycle import AccountData, AutopilotCycle
from adhelper.autopilot.application.gate import GateFacts, PolicyGate
from adhelper.autopilot.domain.decide import Action, ActionKind, Mode
from adhelper.autopilot.domain.health import Window
from adhelper.autopilot.domain.model import AdStats, CampaignStats
from adhelper.autopilot.domain.review import Candidate, ReviewResult, nothing_passed

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
TARGET = 400_000_000
RULES = load_rules("autopilot-1")
POLICY = load_policy_rules("policy-1")
HEALTHY = Window(2_000_000_000, 9, 1000, 900)
BASELINE = Window(10_000_000_000, 50, 5000, 4500)
BAD = AdStats("ad:bad", "c1", "g1", TARGET * 5, 0, 30, True)
GOOD = AdStats("ad:good", "c1", "g1", TARGET * 10, 20, 30, True)
ALL = frozenset(ActionKind)


def facts(**over: Any) -> GateFacts:
    values: dict[str, Any] = {
        "changes_today": 0,
        "moved_this_week": 0,
        "data_alert": False,
        "api_points_rest": 10_000,
        "operating_budget": 1_000_000_000_000,
        "last_limit_change": {},
    }
    return GateFacts(**(values | over))


@dataclass
class Facts:
    value: GateFacts | Exception = field(default_factory=facts)

    async def load(self, account: str, now: datetime) -> GateFacts:
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class Reviewer:
    def __init__(self, passed: bool | None = True) -> None:
        self.passed = passed
        self.seen: list[Sequence[Candidate]] = []

    async def review(self, candidates: Sequence[Candidate]) -> ReviewResult:
        self.seen.append(candidates)
        if self.passed is None:
            return nothing_passed(candidates, "рецензент недоступен", False)
        if self.passed:
            return ReviewResult(frozenset(c.ref for c in candidates))
        return nothing_passed(candidates, "сомнение", True)


class Log:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str | None, str, Mapping[str, Any], str]] = []

    async def record(
        self,
        account: str,
        object_ref: str,
        verdict: str,
        action: str | None,
        status: str,
        inputs: Mapping[str, Any],
        rules_version: str,
        decided_at: datetime,
    ) -> int:
        self.rows.append((object_ref, action, status, dict(inputs), rules_version))
        return len(self.rows)

    async def latest(self, account: str, object_ref: str) -> None:
        return None


class Actions:
    def __init__(self) -> None:
        self.paused: list[str] = []
        self.raised: list[tuple[str, int]] = []

    async def pause_ad(self, account: str, ad_ref: str, explanation: str, decision_id: int) -> str:
        self.paused.append(ad_ref)
        return "applied"

    async def increase_limit(self, account: str, campaign_id: str, new_limit: int) -> str:
        self.raised.append((campaign_id, new_limit))
        return "applied"


class Alerts:
    async def raise_alert(self, kind: str, details: Mapping[str, Any]) -> None: ...
    async def resolve(self, kind: str) -> None: ...


@dataclass
class Source:
    data: AccountData

    async def load(self, account: str, now: datetime) -> AccountData:
        return self.data


def cycle(
    gate: PolicyGate | None, log: Log, actions: Actions, ads: list[AdStats] | None = None
) -> AutopilotCycle:
    data = AccountData(ads or [BAD, GOOD], [], HEALTHY, BASELINE, False, False)
    return AutopilotCycle(Source(data), log, actions, Alerts(), lambda: NOW, RULES, gate=gate)


def gate(source: Facts | None = None, reviewer: Reviewer | None = None) -> PolicyGate:
    return PolicyGate(POLICY, source or Facts(), reviewer)


async def run_self(c: AutopilotCycle) -> None:
    await c.run("acc", TARGET, Mode.AUTO, ALL)


def decision(log: Log) -> tuple[str, str | None, str, Mapping[str, Any], str]:
    return next(row for row in log.rows if row[1] == "pause_ad")


async def test_without_a_gate_behaviour_is_unchanged() -> None:
    log, actions = Log(), Actions()
    await run_self(cycle(None, log, actions))
    assert actions.paused == ["ad:bad"]


async def test_allowed_by_policy_and_reviewer_runs_and_records_rules_version() -> None:
    log, actions, reviewer = Log(), Actions(), Reviewer(True)
    await run_self(cycle(gate(reviewer=reviewer), log, actions))
    assert actions.paused == ["ad:bad"]
    assert reviewer.seen and [c.ref for c in reviewer.seen[0]] == ["ad:bad"]
    inputs = decision(log)[3]
    assert inputs["policy_version"] == "policy-1"
    assert inputs["review"] == "ok"


async def test_policy_refusal_becomes_a_card_with_the_reason_and_reviewer_is_not_asked() -> None:
    log, actions, reviewer = Log(), Actions(), Reviewer(True)
    held = Facts(facts(data_alert=True))
    await run_self(cycle(gate(held, reviewer), log, actions))
    assert actions.paused == []
    row = decision(log)
    assert row[2] == "awaiting_confirmation"
    assert row[3]["held_by"] == "data_alert"
    assert reviewer.seen == []


async def test_reviewer_doubt_becomes_a_card() -> None:
    log, actions = Log(), Actions()
    await run_self(cycle(gate(reviewer=Reviewer(False)), log, actions))
    assert actions.paused == []
    row = decision(log)
    assert row[2] == "awaiting_confirmation"
    assert row[3]["held_by"] == "review_doubt"


async def test_unavailable_reviewer_means_card_not_action() -> None:
    log, actions = Log(), Actions()
    await run_self(cycle(gate(reviewer=Reviewer(None)), log, actions))
    assert actions.paused == []
    assert decision(log)[3]["held_by"] == "review_unavailable"


async def test_gate_failure_is_a_card_not_an_action() -> None:
    log, actions = Log(), Actions()
    broken = Facts(RuntimeError("db down"))
    await run_self(cycle(gate(broken, Reviewer(True)), log, actions))
    assert actions.paused == []
    assert decision(log)[3]["held_by"] == "gate_error"


async def test_policy_without_reviewer_still_guards() -> None:
    log, actions = Log(), Actions()
    await run_self(cycle(gate(Facts(facts(changes_today=99))), log, actions))
    assert actions.paused == []
    assert decision(log)[3]["held_by"] == "daily_limit"


async def test_young_ad_in_learning_is_held() -> None:
    young = AdStats("ad:bad", "c1", "g1", TARGET * 5, 0, 2, True)
    log, actions = Log(), Actions()
    await run_self(cycle(gate(), log, actions, [young, GOOD]))
    assert actions.paused == []
    assert decision(log)[3]["held_by"] == "in_learning"


async def test_class_not_allowed_by_mode_is_a_card_even_with_a_gate() -> None:
    log, actions = Log(), Actions()
    await cycle(gate(reviewer=Reviewer(True)), log, actions).run(
        "acc", TARGET, Mode.AUTO, frozenset({ActionKind.INCREASE_LIMIT})
    )
    assert actions.paused == []


async def test_limit_rise_is_checked_with_campaign_traits_unknown_means_strict() -> None:
    campaign = CampaignStats("c1", 500_000_000, True, None)
    data = AccountData([GOOD], [campaign], HEALTHY, BASELINE, False, False)
    log, actions = Log(), Actions()
    c = AutopilotCycle(Source(data), log, actions, Alerts(), lambda: NOW, RULES, gate=gate())
    await run_self(c)
    # Стратегия и обучение кампании неизвестны: как автостратегия и обучение, сам не повышаю.
    assert actions.raised == []
    rise = next(row for row in log.rows if row[1] == "increase_limit")
    assert rise[2] == "awaiting_confirmation"
    assert rise[3]["held_by"] in {"in_learning", "auto_strategy_interval"}


async def test_limit_rise_runs_when_campaign_is_known_manual_and_calm() -> None:
    campaign = CampaignStats("c1", 500_000_000, True, None, auto_strategy=False, in_learning=False)
    data = AccountData([GOOD], [campaign], HEALTHY, BASELINE, False, False)
    log, actions = Log(), Actions()
    c = AutopilotCycle(
        Source(data), log, actions, Alerts(), lambda: NOW, RULES, gate=gate(reviewer=Reviewer())
    )
    await run_self(c)
    assert actions.raised and actions.raised[0][0] == "c1"


def ad(ref: str, group: str, campaign: str = "c1") -> AdStats:
    return AdStats(ref, campaign, group, TARGET * 5, 0, 30, True)


def pause(ref: str) -> Action:
    return Action(ActionKind.PAUSE_AD, ref, "c1", {"spend_micros": 1}, "autopilot-1", False)


def rise(ref: str, campaign: str, new_limit: int) -> Action:
    return Action(
        ActionKind.INCREASE_LIMIT, ref, campaign, {"leads": 20}, "autopilot-1", False, new_limit
    )


def account(ads: list[AdStats], campaigns: list[CampaignStats] | None = None) -> AccountData:
    return AccountData(ads, campaigns or [], HEALTHY, BASELINE, False, False)


async def test_changes_accumulate_inside_one_cycle() -> None:
    ads = [ad(f"p{k}", f"g{k}") for k in (1, 2, 3)] + [ad(f"q{k}", f"g{k}") for k in (1, 2, 3)]
    notes = await gate(Facts(facts(changes_today=1))).screen(
        "acc", [pause("p1"), pause("p2"), pause("p3")], account(ads), None, NOW
    )
    # Лимит 3 в сутки, одно изменение уже было: проходят два, третье держим.
    assert [notes[ref].held for ref in ("p1", "p2", "p3")] == [False, False, True]
    assert notes["p3"].notes["held_by"] == "daily_limit"


async def test_weekly_share_accumulates_inside_one_cycle() -> None:
    campaigns = [
        CampaignStats(c, 500_000_000, True, None, auto_strategy=False, in_learning=False)
        for c in ("c1", "c2")
    ]
    # Рабочий бюджет 1 000 000 000, потолок недели 20% = 200 000 000; каждый рост 150 000 000.
    notes = await gate(Facts(facts(operating_budget=1_000_000_000))).screen(
        "acc",
        [rise("c1", "c1", 650_000_000), rise("c2", "c2", 650_000_000)],
        account([], campaigns),
        None,
        NOW,
    )
    assert notes["c1"].held is False
    assert notes["c2"].held is True
    assert notes["c2"].notes["held_by"] == "weekly_share"


async def test_empty_allowed_set_allows_nothing() -> None:
    notes = await gate().screen(
        "acc", [pause("p1")], account([ad("p1", "g1"), ad("q1", "g1")]), frozenset(), NOW
    )
    assert notes["p1"].held is True
    assert notes["p1"].notes["held_by"] == "class_not_allowed"


class Exploding:
    async def review(self, candidates: Sequence[Candidate]) -> ReviewResult:
        raise RuntimeError("модель упала")


async def test_reviewer_exception_holds_actions_instead_of_crashing_the_cycle() -> None:
    notes = await PolicyGate(POLICY, Facts(), Exploding()).screen(
        "acc", [pause("p1")], account([ad("p1", "g1"), ad("q1", "g1")]), None, NOW
    )
    assert notes["p1"].held is True
    assert notes["p1"].notes["held_by"] == "review_unavailable"


async def test_duplicate_ref_any_refusal_wins() -> None:
    good = CampaignStats("c1", 500_000_000, True, None, auto_strategy=False, in_learning=False)
    bad = CampaignStats("c2", 500_000_000, True, None, auto_strategy=True, in_learning=False)
    notes = await gate(reviewer=Reviewer(True)).screen(
        "acc",
        [rise("r", "c1", 600_000_000), rise("r", "c2", 600_000_000)],
        account([], [good, bad]),
        None,
        NOW,
    )
    assert notes["r"].held is True


async def test_recent_decrease_counts_for_the_interval() -> None:
    campaign = CampaignStats("c1", 500_000_000, True, None, auto_strategy=True, in_learning=False)
    last = NOW - timedelta(days=3)
    notes = await gate(Facts(facts(last_limit_change={"c1": last}))).screen(
        "acc", [rise("c1", "c1", 600_000_000)], account([], [campaign]), None, NOW
    )
    assert notes["c1"].held is True
    assert notes["c1"].notes["held_by"] == "auto_strategy_interval"
