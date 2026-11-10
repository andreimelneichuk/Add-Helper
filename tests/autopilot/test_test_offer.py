"""Тест по итогам анализа: предложение, согласие и отказ владельца."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.adapters.sql import SqlDecisionLog
from adhelper.autopilot.application.cycle import AutopilotCycle
from adhelper.autopilot.application.proposals import Answer, answer_test, owner_cancelled
from adhelper.autopilot.domain.decide import Mode
from adhelper.autopilot.domain.model import AdStats, CampaignStats
from adhelper.notifications.adapters.sql import SqlAlerts
from tests.autopilot.test_cycle import (
    BUSINESS,
    NOW,
    TARGET,
    Sessions,
    Source,
    _owners,  # noqa: F401  — тот же бизнес и кабинет в базе
    data,
)

RULES = load_rules("autopilot-2")
MIDDLE = AdStats("ad:m", "c1", "c1", TARGET * 3, 3, 30, True)
ROOMY = CampaignStats("c1", TARGET, budget_limited=False, last_change_at=None)


@dataclass
class Planner:
    hypothesis_for: str | None = "price"
    prepared: list[tuple[str, str, str, str, int]] = field(default_factory=list)

    async def hypothesis(self, account: str, ad_ref: str) -> str | None:
        return self.hypothesis_for

    async def prepare(
        self, account: str, campaign_id: str, ad_ref: str, hypothesis: str, decision_id: int
    ) -> str:
        self.prepared.append((account, campaign_id, ad_ref, hypothesis, decision_id))
        return "prepared"


class NoActions:
    async def pause_ad(self, account: str, ad_ref: str, explanation: str, decision_id: int) -> str:
        raise AssertionError("тест не трогает показы")

    async def increase_limit(
        self, account: str, campaign_id: str, new_limit: int, donors: Sequence[str] = ()
    ) -> str:
        raise AssertionError("тест не трогает лимит")


def cycle(sessions: Sessions, planner: Planner) -> AutopilotCycle:
    return AutopilotCycle(
        Source(data([MIDDLE], [ROOMY])),
        SqlDecisionLog(sessions, BUSINESS),
        NoActions(),
        SqlAlerts(sessions, BUSINESS),
        lambda: NOW,
        RULES,
        planner,
    )


async def latest(sessions: Sessions) -> dict[str, object]:
    item = await SqlDecisionLog(sessions, BUSINESS).latest("acc", "ad:m")
    assert item is not None
    return {"id": item.id, "status": item.status, **item.inputs}


async def test_confirm_mode_proposes_test_with_hypothesis_and_numbers(sessions: Sessions) -> None:
    planner = Planner()

    await cycle(sessions, planner).run("acc", TARGET, Mode.CONFIRM)

    decision = await latest(sessions)
    assert decision["status"] == "awaiting_confirmation"
    assert decision["hypothesis"] == "price"
    assert decision["spend_micros"] == TARGET * 3
    assert decision["limit_micros"] == TARGET
    assert planner.prepared == []


async def test_without_supported_hypothesis_nothing_is_proposed(sessions: Sessions) -> None:
    await cycle(sessions, Planner(hypothesis_for=None)).run("acc", TARGET, Mode.CONFIRM)

    assert (await latest(sessions))["status"] == "no_hypothesis"


async def test_auto_mode_prepares_test_itself(sessions: Sessions) -> None:
    planner = Planner()

    await cycle(sessions, planner).run("acc", TARGET, Mode.AUTO)

    assert [p[:4] for p in planner.prepared] == [("acc", "c1", "ad:m", "price")]
    assert (await latest(sessions))["status"] == "prepared"


async def test_owner_agrees_and_test_is_prepared(sessions: Sessions) -> None:
    planner = Planner()
    await cycle(sessions, planner).run("acc", TARGET, Mode.CONFIRM)
    proposal = (await latest(sessions))["id"]
    log = SqlDecisionLog(sessions, BUSINESS)

    answer = await answer_test(log, planner, int(str(proposal)), True, NOW)

    assert answer is Answer.PREPARED
    assert [p[:4] for p in planner.prepared] == [("acc", "c1", "ad:m", "price")]
    assert (await latest(sessions))["status"] == "prepared"


async def test_owner_declines_and_decision_remembers_it(sessions: Sessions) -> None:
    planner = Planner()
    await cycle(sessions, planner).run("acc", TARGET, Mode.CONFIRM)
    proposal = int(str((await latest(sessions))["id"]))

    answer = await answer_test(SqlDecisionLog(sessions, BUSINESS), planner, proposal, False, NOW)

    assert answer is Answer.DECLINED
    assert planner.prepared == []
    assert (await latest(sessions))["status"] == "declined"


async def test_old_proposal_cannot_be_answered_twice(sessions: Sessions) -> None:
    planner = Planner()
    await cycle(sessions, planner).run("acc", TARGET, Mode.CONFIRM)
    proposal = int(str((await latest(sessions))["id"]))
    log = SqlDecisionLog(sessions, BUSINESS)
    await answer_test(log, planner, proposal, True, NOW)

    assert await answer_test(log, planner, proposal, True, NOW) is Answer.STALE
    assert await answer_test(log, planner, proposal, False, NOW) is Answer.STALE
    assert len(planner.prepared) == 1


async def test_unknown_proposal_is_not_found(sessions: Sessions) -> None:
    log = SqlDecisionLog(sessions, BUSINESS)
    assert await answer_test(log, Planner(), 10**9, True, NOW) is Answer.NOT_FOUND


async def test_owner_cancel_of_prepared_test_counts_as_decline(sessions: Sessions) -> None:
    # Отмена владельцем — тот же отказ: автопилот не предлагает это объявление на срок отказа.
    planner = Planner()
    await cycle(sessions, planner).run("acc", TARGET, Mode.CONFIRM)
    proposal = int(str((await latest(sessions))["id"]))
    log = SqlDecisionLog(sessions, BUSINESS)
    await answer_test(log, planner, proposal, True, NOW)

    assert await owner_cancelled(log, "acc", "ad:m", NOW)

    assert (await latest(sessions))["status"] == "declined"
    assert not await owner_cancelled(log, "acc", "ad:other", NOW)
