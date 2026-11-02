"""Выборки для агентов: написанные объявления, голоса, согласие и хранение. Модель не вызывается."""

import json
from collections import Counter
from pathlib import Path

import pytest
from evals.dataset.build import (
    BadReply,
    Dataset,
    ItemRow,
    LabelRow,
    VoteRow,
    append_jsonl,
    blind_key,
    gold,
    load,
    parse_ads,
    parse_votes,
    split_of,
    to_cases,
    upper_bound,
    usable,
)
from evals.dataset.spec import (
    AD_KINDS,
    AUTHOR,
    KINDS_BY_CATEGORY,
    NICHES,
    PANEL,
    SERVICE_MODELS,
    SPLITS,
)

from adhelper.creatives.domain.model import ViolationKind

AUTHORED = load(Path("/nonexistent"))
# Код склеивает число из названия услуги с ценой (R13–R15 + 1600): известная ошибка проверки.
KNOWN_CODE_FALSE_BLOCKS = {"tires-00-01", "tires-00-03", "photo-01-03"}


def test_spec_is_consistent() -> None:
    assert sum(share for _, share in SPLITS) == 100
    assert len({n.slug for n in NICHES}) == len(NICHES)
    assert {kind.label for kind in AD_KINDS} == {"pass", "block"}
    assert PANEL[0] == AUTHOR and len(set(PANEL)) == len(PANEL)
    # Модель сервиса не может размечать правду о самой себе.
    assert not set(PANEL) & set(SERVICE_MODELS)


def test_authored_profiles_cover_every_niche_and_split() -> None:
    niches = {profile.niche for profile in AUTHORED.profiles.values()}
    assert niches == {niche.name for niche in NICHES}
    splits = Counter(item.split for item in AUTHORED.items.values())
    assert all(splits[name] >= 60 for name, _ in SPLITS), splits


def test_authored_ads_cover_every_kind() -> None:
    kinds = Counter(item.category for item in AUTHORED.items.values())
    assert set(kinds) == set(KINDS_BY_CATEGORY)
    assert min(kinds.values()) >= 10, kinds


def test_authored_ads_fit_platform_and_mark_violation() -> None:
    seen = set()
    for item in AUTHORED.items.values():
        assert usable(item.code_violations), item.id
        key = (item.headline, item.text)
        assert key not in seen, item.id
        seen.add(key)
        label = AUTHORED.votes[item.id][AUTHOR].label
        if KINDS_BY_CATEGORY[item.category].label == "block":
            assert item.span and item.span in f"{item.headline} {item.text}", item.id
            assert label == "block", item.id
        else:
            assert label in {"pass", "ambiguous"}, item.id


def test_authored_clean_ads_have_only_profile_numbers() -> None:
    # Код надёжно сверяет числа, поэтому допустимое объявление с чужим числом — ошибка автора.
    for item in AUTHORED.items.values():
        if AUTHORED.votes[item.id][AUTHOR].label == "block":
            continue
        if item.id in KNOWN_CODE_FALSE_BLOCKS:
            continue
        assert ViolationKind.UNCONFIRMED_NUMBER.value not in item.code_violations, item.id


def test_parse_ads_and_votes() -> None:
    assert parse_ads(json.dumps({"ads": [{"headline": " А ", "text": "Б"}, "x"]})) == [("А", "Б")]
    with pytest.raises(BadReply):
        parse_ads("[]")
    reply = json.dumps(
        {
            "votes": [
                {"n": 1, "label": "block", "reason": "скидка"},
                {"n": 2, "label": "maybe"},
                {"n": 9, "label": "pass"},
                {"n": "3", "label": "pass"},
            ]
        }
    )
    assert parse_votes(reply, 3) == {1: ("block", "скидка")}


def test_split_depends_only_on_profile() -> None:
    ids = [f"nails-{i:02d}" for i in range(400)]
    first = [split_of("seed", i) for i in ids]
    assert first == [split_of("seed", i) for i in ids]
    counts = Counter(first)
    for name, share in SPLITS:
        assert abs(counts[name] - 4 * share) < 40, counts


def _vote(item_id: str, labeler: str, label: str) -> VoteRow:
    return VoteRow(item_id, labeler, label, "", "v", "t")  # type: ignore[arg-type]


def test_gold_needs_full_agreement_or_human() -> None:
    data = Dataset()
    for labeler in PANEL:
        data.add_vote(_vote("a", labeler, "block"))
        data.add_vote(_vote("b", labeler, "pass" if labeler == AUTHOR else "block"))
    data.add_vote(_vote("c", AUTHOR, "pass"))
    assert gold(data, "a") == ("block", "consensus")
    assert gold(data, "b") == (None, "disputed")
    assert gold(data, "c") == (None, "incomplete")
    data.labels["b"] = LabelRow("b", "pass", "человек", "t")
    assert gold(data, "b") == ("pass", "human")


def test_upper_bound_matches_docs() -> None:
    # Цифры из docs/12-agents.md §2.
    assert round(upper_bound(1, 49) * 100, 1) == 9.3
    assert round(upper_bound(0, 299) * 100, 2) == pytest.approx(1.0, abs=0.01)
    assert upper_bound(0, 0) == 1.0


def test_store_roundtrip_and_cases(tmp_path: Path) -> None:
    profile_id = next(iter(AUTHORED.profiles))
    writer = ItemRow(
        f"{profile_id}-w0", "a1", "test", profile_id, "writer", "writer",
        "Заголовок", "Текст", "", (), "m", "v",
    )  # fmt: skip
    append_jsonl(tmp_path / "writer.jsonl", [writer])
    append_jsonl(tmp_path / "votes.jsonl", [_vote(writer.id, name, "pass") for name in PANEL])
    append_jsonl(tmp_path / "labels.jsonl", [LabelRow(writer.id, "block", "человек", "t")])
    data = load(tmp_path)
    assert data.items[writer.id] == writer
    assert gold(data, writer.id) == ("block", "human")
    cases, profiles = to_cases(data, "test", "human")
    assert [(c.id, c.label) for c in cases] == [(writer.id, "block")]
    assert profile_id in profiles
    authored_cases, _ = to_cases(data, "train", "author")
    assert len(authored_cases) == sum(1 for i in AUTHORED.items.values() if i.split == "train")


def test_blind_key_hides_item_and_differs_by_labeler() -> None:
    item_id = next(iter(AUTHORED.items))
    key = blind_key("claude-b", item_id)
    assert key == blind_key("claude-b", item_id)
    assert key != blind_key("claude-c", item_id)
    assert item_id.split("-")[0] not in key and len(key) == 8
