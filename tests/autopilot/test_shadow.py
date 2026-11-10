"""Теневой режим autopilot-3: считает рядом с рабочими правилами и пишет «сделала бы»."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from adhelper.autopilot.adapters.rules_file import load_rules
from adhelper.autopilot.application import cycle as cycle_module
from adhelper.autopilot.application.cycle import AccountData, AutopilotCycle
from adhelper.autopilot.domain.decide import Mode
from adhelper.autopilot.domain.health import Window
from adhelper.autopilot.domain.model import AdStats
from adhelper.autopilot.domain.rules import AutopilotRules

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
TARGET = 300_000_000
WORKING = load_rules("autopilot-2")
SHADOW = load_rules("autopilot-3")
HEALTHY = Window(2_000_000_000, 9, 1000, 900)
BASELINE = Window(10_000_000_000, 50, 5000, 4500)


def ad(ref: str, clicks: int, leads: int) -> AdStats:
    return AdStats(ref, "c1", ref, clicks * 5_000_000, leads, 9, True, clicks=clicks)


@dataclass
class Source:
    data: AccountData

    async def load(self, account: str, now: datetime) -> AccountData:
        return self.data


@dataclass
class Last:
    decided_at: datetime
    inputs: Mapping[str, Any]
    verdict: str
    action: str | None


@dataclass
class Log:
    rows: list[dict[str, Any]] = field(default_factory=list)

    async def latest(self, account: str, object_ref: str) -> Last | None:
        for row in reversed(self.rows):
            if row["ref"] == object_ref:
                return Last(row["at"], row["inputs"], row["verdict"], row["action"])
        return None

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
        self.rows.append(
            {
                "ref": object_ref,
                "verdict": verdict,
                "action": action,
                "status": status,
                "inputs": dict(inputs),
                "rules": rules_version,
                "at": decided_at,
            }
        )
        return len(self.rows)


@dataclass
class Hands:
    paused: list[str] = field(default_factory=list)
    raised: list[str] = field(default_factory=list)

    async def pause_ad(self, account: str, ad_ref: str, explanation: str, decision_id: int) -> str:
        self.paused.append(ad_ref)
        return "applied"

    async def increase_limit(
        self, account: str, campaign_id: str, new_limit: int, donors: Sequence[str] = ()
    ) -> str:
        return "applied"

    async def raise_alert(self, kind: str, details: Mapping[str, Any]) -> None:
        self.raised.append(kind)

    async def resolve(self, kind: str) -> None:
        return None


def build(
    ads: list[AdStats],
    log: Log,
    hands: Hands,
    *,
    incident: bool = False,
    shadow: AutopilotRules | None = SHADOW,
) -> AutopilotCycle:
    data = AccountData(ads, [], HEALTHY, BASELINE, incident, False)
    return AutopilotCycle(
        Source(data), log, hands, hands, lambda: NOW, WORKING, shadow_rules=shadow
    )


async def test_shadow_records_would_pause_without_any_action_or_card() -> None:
    log, hands = Log(), Hands()
    await build([ad("ad:a", 400, 20), ad("ad:b", 400, 10)], log, hands).run(
        "acc", TARGET, Mode.AUTO
    )

    shadow = [row for row in log.rows if row["status"] == "shadow"]
    would = next(row for row in shadow if row["ref"] == "shadow:ad:b")
    assert would["action"] == "pause_ad" and would["rules"] == "autopilot-3"
    inputs = would["inputs"]
    assert inputs["better_ref"] == "ad:a" and inputs["would"] == "pause_ad"
    assert inputs["p_worse_bp"] >= SHADOW.min_worse_probability_bp
    assert inputs["loss_micros"] > inputs["error_cost_micros"]
    assert inputs["goal_micros"] == TARGET
    assert hands.paused == []
    # Рабочие правила записали своё, под своей версией и без префикса.
    assert all(row["rules"] == "autopilot-2" for row in log.rows if row["status"] != "shadow")
    assert all(row["ref"].startswith("shadow:") for row in shadow)
    assert not [row for row in shadow if row["status"] == "awaiting_confirmation"]


async def test_shadow_never_executes_anything() -> None:
    log, hands = Log(), Hands()
    ads = [ad("ad:a", 400, 20), ad("ad:b", 400, 10)]
    # Рабочие правила в режиме «Предлагает» ничего не делают сами.
    await build(ads, log, hands).run("acc", TARGET, Mode.CONFIRM)
    assert hands.paused == []


async def test_shadow_says_why_when_it_would_wait() -> None:
    log, hands = Log(), Hands()
    await build([ad("ad:a", 400, 20), ad("ad:b", 100, 3)], log, hands).run(
        "acc", TARGET, Mode.CONFIRM
    )

    row = next(r for r in log.rows if r["ref"] == "shadow:ad:b")
    assert row["status"] == "shadow" and row["action"] is None
    assert row["inputs"]["reason"] == "low_data" and row["inputs"]["would"] == "wait"


async def test_shadow_in_bad_data_is_protect_only_like_the_working_rules() -> None:
    log, hands = Log(), Hands()
    await build([ad("ad:a", 400, 40), ad("ad:b", 400, 0)], log, hands, incident=True).run(
        "acc", TARGET, Mode.AUTO
    )
    shadow = [row for row in log.rows if row["status"] == "shadow"]
    assert [(r["ref"], r["verdict"], r["inputs"]["would"]) for r in shadow] == [
        ("shadow:acc", "untrusted", "protect_only")
    ]
    assert hands.paused == []


async def test_without_shadow_rules_nothing_extra_is_written() -> None:
    log, hands = Log(), Hands()
    await build([ad("ad:a", 400, 20), ad("ad:b", 400, 10)], log, hands, shadow=None).run(
        "acc", TARGET, Mode.CONFIRM
    )
    assert not [row for row in log.rows if row["status"] == "shadow"]


async def test_shadow_failure_never_breaks_the_working_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("сбой теневого счёта")

    monkeypatch.setattr(cycle_module, "plan_cycle_ev", broken)
    log, hands = Log(), Hands()
    await build([ad("ad:a", 400, 20), ad("ad:b", 400, 10)], log, hands).run(
        "acc", TARGET, Mode.AUTO
    )

    assert log.rows, "рабочие правила отработали и записали решения"
    assert not [row for row in log.rows if row["status"] == "shadow"]


async def test_same_day_same_inputs_are_not_written_twice_but_changes_are() -> None:
    log, hands = Log(), Hands()
    ads = [ad("ad:a", 400, 20), ad("ad:b", 400, 10)]
    cycle = build(ads, log, hands)
    await cycle.run("acc", TARGET, Mode.CONFIRM)
    first = [r for r in log.rows if r["status"] == "shadow"]
    await cycle.run("acc", TARGET, Mode.CONFIRM)
    assert [r for r in log.rows if r["status"] == "shadow"] == first
    # Изменились входные данные: запись новая.
    await build([ad("ad:a", 400, 20), ad("ad:b", 400, 14)], log, hands).run(
        "acc", TARGET, Mode.CONFIRM
    )
    assert len([r for r in log.rows if r["status"] == "shadow"]) > len(first)


async def test_bad_data_shadow_record_carries_the_health_window() -> None:
    log, hands = Log(), Hands()
    await build([ad("ad:a", 400, 40)], log, hands, incident=True).run("acc", TARGET, Mode.AUTO)
    inputs = next(r for r in log.rows if r["ref"] == "shadow:acc")["inputs"]
    assert (
        inputs["recent_clicks"] == HEALTHY.clicks and inputs["baseline_clicks"] == BASELINE.clicks
    )
    assert inputs["platform_incident"] == 1 and inputs["reasons"]
