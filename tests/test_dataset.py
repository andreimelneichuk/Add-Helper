"""Выборки для агентов: разбор ответов, фильтры, разбиение и хранение. Модель не вызывается."""

import json
from pathlib import Path

import pytest
from evals.dataset.build import (
    BadReply,
    ItemRow,
    LabelRow,
    ProfileRow,
    append_jsonl,
    load,
    parse_ads,
    parse_profile,
    pick_kinds,
    split_of,
    to_cases,
    usable,
)
from evals.dataset.spec import AD_KINDS, NICHES, SPLITS

PROFILE = {
    "name": "Лотос",
    "services": [{"name": "Маникюр", "price": 800}, {"name": "Дизайн ногтей", "price": None}],
    "address": "ул. Солнечная, 12",
    "features": ["Работаем без выходных", "Парковка рядом"],
}


def _item(item_id: str, split: str, label: str | None, violations: tuple[str, ...] = ()) -> ItemRow:
    return ItemRow(
        item_id, "b1", split, "nails-00", "injected", "exact_fact", label,  # type: ignore[arg-type]
        "Маникюр за 800 ₽", "ул. Солнечная, 12", "", violations, "m", "v",
    )  # fmt: skip


def test_spec_is_consistent() -> None:
    assert sum(share for _, share in SPLITS) == 100
    assert len({n.slug for n in NICHES}) == len(NICHES)
    labels = {kind.label for kind in AD_KINDS}
    assert labels == {"pass", "block"}
    assert len({kind.category for kind in AD_KINDS}) == len(AD_KINDS)


def test_parse_profile_accepts_valid_reply() -> None:
    name, services, address, features = parse_profile(json.dumps(PROFILE, ensure_ascii=False))
    assert name == "Лотос"
    assert services == (("Маникюр", 800), ("Дизайн ногтей", None))
    assert address and len(features) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"services": [{"name": "Маникюр", "price": 800}]},
        {"services": [{"name": "Маникюр", "price": 80.5}, {"name": "Педикюр", "price": 900}]},
        {"services": [{"name": "Маникюр", "price": None}, {"name": "Педикюр", "price": None}]},
        {"services": [{"name": "Маникюр", "price": True}, {"name": "Педикюр", "price": 900}]},
        {"features": ["Одна"]},
        {"name": "Nail Bar"},
        {"name": ""},
    ],
)
def test_parse_profile_rejects_bad_reply(change: dict[str, object]) -> None:
    with pytest.raises(BadReply):
        parse_profile(json.dumps(PROFILE | change, ensure_ascii=False))


def test_parse_ads_drops_unknown_categories() -> None:
    reply = json.dumps(
        {
            "ads": [
                {"category": "superlative", "headline": "Лучший", "text": "т", "span": "Лучший"},
                {"category": "nonsense", "headline": "x", "text": "y"},
                "мусор",
            ]
        }
    )
    assert parse_ads(reply, with_category=True) == [("superlative", "Лучший", "т", "Лучший")]
    with pytest.raises(BadReply):
        parse_ads("[]", with_category=True)


def test_split_depends_only_on_profile() -> None:
    ids = [f"nails-{i:02d}" for i in range(400)]
    first = [split_of("seed", i) for i in ids]
    assert first == [split_of("seed", i) for i in ids]
    counts = {name: first.count(name) for name, _ in SPLITS}
    for name, share in SPLITS:
        assert abs(counts[name] - 4 * share) < 40, counts


def test_pick_kinds_is_stable_subset() -> None:
    categories = [kind.category for kind in AD_KINDS]
    picked = pick_kinds("seed", "nails-00", categories, 8)
    assert picked == pick_kinds("seed", "nails-00", list(reversed(categories)), 8)
    assert len(picked) == 8 and set(picked) <= set(categories)


def test_unusable_drafts_are_filtered() -> None:
    assert usable(["forbidden_word"])
    assert not usable(["too_long"])


def test_store_roundtrip_and_human_labels_win(tmp_path: Path) -> None:
    profile = ProfileRow(
        "nails-00", "b1", "маникюр", "эконом", "Лотос", (("Маникюр", 800),),
        "ул. Солнечная, 12", ("Парковка рядом",), "m",
    )  # fmt: skip
    append_jsonl(tmp_path / "profiles.jsonl", [profile])
    append_jsonl(
        tmp_path / "items.jsonl",
        [
            _item("a", "test", "pass"),
            _item("b", "test", "block"),
            _item("c", "train", "block"),
            _item("d", "test", None),
            _item("e", "test", "pass", ("too_long",)),
        ],
    )
    append_jsonl(
        tmp_path / "labels.jsonl",
        [
            LabelRow("a", "pass", "человек", "t"),
            LabelRow("a", "block", "человек", "t2", "передумал"),
            LabelRow("d", "pass", "человек", "t"),
        ],
    )
    data = load(tmp_path)
    assert data.profiles["nails-00"] == profile
    human, profiles = to_cases(data, "test")
    assert [(c.id, c.label) for c in human] == [("a", "block"), ("d", "pass")]
    assert profiles["nails-00"].services[0].price == 800
    proposed, _ = to_cases(data, "test", labels="proposed")
    assert [(c.id, c.label) for c in proposed] == [("a", "block"), ("b", "block"), ("d", "pass")]
    everything, _ = to_cases(data, "all", labels="proposed")
    assert "c" in {c.id for c in everything}
