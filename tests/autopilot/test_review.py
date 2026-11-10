"""Рецензент автопилота: только останавливает, ничего не добавляет (docs/12-agents.md)."""

import json
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from adhelper.autopilot.adapters.reviewer import ModelReviewer
from adhelper.autopilot.domain.review import Candidate, ReviewResult, build_request, parse_reply

CANDS = (
    Candidate("ad-1", "pause_ad", "c1", {"spend_micros": 900_000_000, "leads": 0}, None),
    Candidate("c1", "increase_limit", "c1", {"leads": 12}, 600_000_000),
)


def reply(*verdicts: dict[str, object]) -> str:
    return json.dumps({"verdicts": list(verdicts)})


def test_ok_for_every_candidate_lets_them_run_themselves() -> None:
    result = parse_reply(
        reply(
            {"ref": "ad-1", "verdict": "ok", "reason": ""},
            {"ref": "c1", "verdict": "ok", "reason": ""},
        ),
        CANDS,
    )
    assert result.available
    assert result.passed == frozenset({"ad-1", "c1"})
    assert result.doubts == {}


def test_doubt_stops_the_action_and_keeps_the_reason() -> None:
    result = parse_reply(
        reply(
            {"ref": "ad-1", "verdict": "doubt", "reason": "мало данных"},
            {"ref": "c1", "verdict": "ok", "reason": ""},
        ),
        CANDS,
    )
    assert result.passed == frozenset({"c1"})
    assert result.doubts == {"ad-1": "мало данных"}


def test_extra_actions_from_the_model_are_dropped() -> None:
    result = parse_reply(
        reply(
            {"ref": "ad-1", "verdict": "ok", "reason": ""},
            {"ref": "c1", "verdict": "ok", "reason": ""},
            {"ref": "ad-999", "verdict": "ok", "reason": ""},
        ),
        CANDS,
    )
    assert result.passed <= {c.ref for c in CANDS}


def test_candidate_without_verdict_is_not_passed() -> None:
    result = parse_reply(reply({"ref": "ad-1", "verdict": "ok", "reason": ""}), CANDS)
    assert result.passed == frozenset({"ad-1"})
    assert "c1" in result.doubts


def test_any_doubt_on_a_duplicated_ref_wins() -> None:
    result = parse_reply(
        reply(
            {"ref": "ad-1", "verdict": "ok", "reason": ""},
            {"ref": "ad-1", "verdict": "doubt", "reason": "странно"},
            {"ref": "c1", "verdict": "ok", "reason": ""},
        ),
        CANDS,
    )
    assert "ad-1" in result.doubts and "ad-1" not in result.passed


@pytest.mark.parametrize(
    "text",
    [
        "",
        "не json",
        "[]",
        "{}",
        json.dumps({"verdicts": "ok"}),
        json.dumps({"verdicts": [{"ref": "ad-1", "verdict": "maybe", "reason": ""}]}),
        json.dumps({"verdicts": [{"ref": 1, "verdict": "ok"}]}),
    ],
)
def test_invalid_reply_passes_nothing(text: str) -> None:
    result = parse_reply(text, CANDS)
    assert result.passed == frozenset()
    assert set(result.doubts) == {c.ref for c in CANDS}


def test_model_cannot_widen_the_action() -> None:
    wide = json.dumps(
        {"verdicts": [{"ref": "c1", "verdict": "ok", "reason": "", "new_limit_micros": 10**12}]}
    )
    result = parse_reply(wide, CANDS)
    assert not hasattr(result, "new_limit")
    assert result.passed == frozenset({"c1"})


def test_request_has_only_numbers_and_ids_no_secrets_or_site_text() -> None:
    body = build_request(CANDS)
    data = json.loads(body)
    assert [c["ref"] for c in data["candidates"]] == ["ad-1", "c1"]
    for forbidden in ("token", "inn", "http", "site", "password"):
        assert forbidden not in body.lower()
    assert all(isinstance(v, int) for c in data["candidates"] for v in c["facts"].values())


@dataclass
class Reply:
    text: str


class FakeModel:
    def __init__(self, text: str | Exception) -> None:
        self.text = text
        self.calls: list[tuple[str, str]] = []

    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> Reply:
        self.calls.append((system, user))
        if isinstance(self.text, Exception):
            raise self.text
        return Reply(self.text)


async def run(model: FakeModel, cands: Sequence[Candidate] = CANDS) -> ReviewResult:
    return await ModelReviewer(model).review(cands)


async def test_adapter_sends_the_request_and_parses_the_reply() -> None:
    model = FakeModel(reply({"ref": "ad-1", "verdict": "ok", "reason": ""}))
    result = await run(model, CANDS[:1])
    assert result.passed == frozenset({"ad-1"})
    assert json.loads(model.calls[0][1]) == json.loads(build_request(CANDS[:1]))


async def test_unavailable_model_passes_nothing_and_does_not_raise() -> None:
    result = await run(FakeModel(RuntimeError("503")))
    assert not result.available
    assert result.passed == frozenset()
    assert set(result.doubts) == {"ad-1", "c1"}


async def test_no_candidates_means_no_call() -> None:
    model = FakeModel("{}")
    result = await ModelReviewer(model).review(())
    assert model.calls == []
    assert result.passed == frozenset()


async def test_reply_that_cannot_be_parsed_at_all_passes_nothing_and_does_not_raise() -> None:
    deep = "[" * 100_000
    for text in (deep, "null", '{"verdicts": [null]}'):
        result = await run(FakeModel(text))
        assert result.passed == frozenset()


class NoText:
    pass


async def test_reply_without_text_passes_nothing() -> None:
    class Model:
        async def complete_json(
            self, system: str, user: str, schema: dict[str, object], max_tokens: int
        ) -> NoText:
            return NoText()

    result = await ModelReviewer(Model()).review(CANDS)  # type: ignore[arg-type]
    assert not result.available
    assert result.passed == frozenset()


def test_request_adds_readable_integer_percentages_computed_by_code() -> None:
    cand = Candidate(
        "c:1",
        "increase_limit",
        "c1",
        {
            "spend_micros": 2_000_000_000,
            "target_micros": 400_000_000,
            "leads": 12,
            "p_good_bp": 9350,
            "p_bad_bp": 480,
            "limit_micros": 500_000_000,
        },
        600_000_000,
    )
    item = json.loads(build_request([cand]))["candidates"][0]
    assert item["spend_percent_of_target"] == 500
    assert (item["leads"], item["p_good_percent"], item["p_bad_percent"]) == (12, 93, 4)
    assert item["limit_growth_percent"] == 20
    assert all(isinstance(v, int) for v in item.values() if not isinstance(v, (str, dict)))


def test_request_without_known_facts_has_no_readable_fields() -> None:
    item = json.loads(build_request(CANDS[:1]))["candidates"][0]
    assert "spend_percent_of_target" not in item and "limit_growth_percent" not in item
