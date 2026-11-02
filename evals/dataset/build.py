"""Чистые функции набора: сборка, выборки, голоса, согласие, хранение. Сеть не нужна.

Источник истины:
- evals/dataset/authored/ — профили и объявления, написанные вручную, с меткой автора;
- evals/data/writer.jsonl — объявления настоящего писателя сервиса по этим профилям;
- evals/data/votes.jsonl — голоса разметчиков-моделей и автора по объявлениям писателя;
- evals/data/labels.jsonl — метки человека.
Файлы в evals/data только дополняются, последняя запись по паре (id, разметчик) побеждает.
"""

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from adhelper.creatives.domain.checks import check_draft
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    CreativeRules,
    Service,
    ViolationKind,
)
from evals.cases import Case
from evals.dataset.spec import AUTHOR, PANEL, SPLITS, Label, SplitName

DATA = Path(__file__).resolve().parent.parent / "data"
LABELS: tuple[Label, ...] = ("pass", "block", "ambiguous")
# Такие черновики площадка не примет, до судьи они не доходят.
UNUSABLE = frozenset({ViolationKind.TOO_LONG, ViolationKind.EMPTY})
GoldSource = Literal["human", "consensus", "disputed", "incomplete"]


class BadReply(ValueError):
    """Ответ модели не подходит под ожидаемую форму."""


@dataclass(frozen=True)
class ProfileRow:
    id: str
    batch: str
    niche: str
    style: str
    name: str
    services: tuple[tuple[str, int | None], ...]
    address: str
    features: tuple[str, ...]

    def business(self) -> BusinessProfile:
        return BusinessProfile(
            self.name,
            tuple(Service(name, price) for name, price in self.services),
            self.address,
            self.features,
            self.niche,
        )


@dataclass(frozen=True)
class ItemRow:
    id: str
    batch: str
    split: SplitName
    profile_id: str
    # authored — написано вручную под заданный вид, метка автора известна;
    # writer — настоящий промпт писателя из сервиса, метку дают разметчики.
    origin: str
    category: str
    headline: str
    text: str
    # Фрагмент с нарушением по замыслу автора. Разметчикам не показывается.
    span: str
    code_violations: tuple[str, ...]
    model: str
    prompt_version: str


@dataclass(frozen=True)
class VoteRow:
    id: str
    labeler: str
    label: Label
    reason: str
    prompt_version: str
    at: str


@dataclass(frozen=True)
class LabelRow:
    id: str
    label: Label
    labeler: str
    at: str
    note: str = ""


@dataclass
class Dataset:
    profiles: dict[str, ProfileRow] = field(default_factory=dict)
    items: dict[str, ItemRow] = field(default_factory=dict)
    votes: dict[str, dict[str, VoteRow]] = field(default_factory=dict)
    labels: dict[str, LabelRow] = field(default_factory=dict)

    def add_vote(self, vote: VoteRow) -> None:
        self.votes.setdefault(vote.id, {})[vote.labeler] = vote


# --- разбор ответов модели ---


def _loads_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError as error:
        raise BadReply("not_json") from error
    if not isinstance(data, dict):
        raise BadReply("not_object")
    return cast(dict[str, Any], data)


def parse_ads(text: str) -> list[tuple[str, str]]:
    """Ответ писателя: (заголовок, текст)."""
    ads = _loads_object(text).get("ads")
    if not isinstance(ads, list):
        raise BadReply("no_ads")
    return [
        (str(ad.get("headline", "")).strip(), str(ad.get("text", "")).strip())
        for ad in ads
        if isinstance(ad, dict)
    ]


def parse_votes(text: str, count: int) -> dict[int, tuple[Label, str]]:
    """Ответ разметчика: номер объявления → метка и причина. Чужие номера отбрасываются."""
    votes = _loads_object(text).get("votes")
    if not isinstance(votes, list):
        raise BadReply("no_votes")
    result: dict[int, tuple[Label, str]] = {}
    for vote in votes:
        if not isinstance(vote, dict):
            continue
        number, label = vote.get("n"), vote.get("label")
        if isinstance(number, int) and 1 <= number <= count and label in LABELS:
            result[number] = (cast(Label, label), str(vote.get("reason", ""))[:200])
    return result


# --- фильтры и выборки ---


def normalized(headline: str, text: str) -> str:
    return " ".join(re.findall(r"[а-яёa-z0-9]+", f"{headline} {text}".lower()))


def code_violations(draft: AdDraft, profile: BusinessProfile, rules: CreativeRules) -> list[str]:
    return sorted({v.kind.value for v in check_draft(draft, profile, rules)})


def usable(violations: Iterable[str]) -> bool:
    return not {ViolationKind(v) for v in violations} & UNUSABLE


def bucket(seed: str, key: str) -> int:
    digest = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100


def split_of(seed: str, profile_id: str) -> SplitName:
    """Выборка зависит только от профиля и зерна: новые партии не двигают старые записи."""
    value = bucket(seed, profile_id)
    edge = 0
    for name, share in SPLITS:
        edge += share
        if value < edge:
            return name
    raise AssertionError("доли выборок должны давать 100")


def prompt_version(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:10]


# --- согласие разметчиков ---


def gold(
    data: Dataset, item_id: str, panel: Sequence[str] = PANEL
) -> tuple[Label | None, GoldSource]:
    """Метка человека главнее всего. Без неё правдой считаем только единогласие всей панели:
    модели ошибаются похоже, поэтому любое расхождение уходит человеку."""
    human = data.labels.get(item_id)
    if human:
        return human.label, "human"
    votes = data.votes.get(item_id, {})
    if any(labeler not in votes for labeler in panel):
        return None, "incomplete"
    labels = {votes[labeler].label for labeler in panel}
    if len(labels) == 1:
        return labels.pop(), "consensus"
    return None, "disputed"


def upper_bound(errors: int, total: int, confidence: float = 0.95) -> float:
    """Односторонняя верхняя граница Клоппера — Пирсона для доли ошибок, как в docs/12 §2."""
    if total == 0 or errors >= total:
        return 1.0

    def cdf(p: float) -> float:
        return sum(math.comb(total, k) * p**k * (1 - p) ** (total - k) for k in range(errors + 1))

    low, high = errors / total, 1.0
    for _ in range(60):
        middle = (low + high) / 2
        if cdf(middle) > 1 - confidence:
            low = middle
        else:
            high = middle
    return high


# --- хранение ---


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        cast(dict[str, Any], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load(root: Path = DATA, authored: bool = True) -> Dataset:
    data = Dataset()
    if authored:
        from evals.dataset.authored import build_authored

        build_authored(data)
    for row in _read_jsonl(root / "writer.jsonl"):
        item = ItemRow(**{**row, "code_violations": tuple(row["code_violations"])})
        data.items[item.id] = item
    for row in _read_jsonl(root / "votes.jsonl"):
        data.add_vote(VoteRow(**row))
    for row in _read_jsonl(root / "labels.jsonl"):
        data.labels[str(row["id"])] = LabelRow(**row)
    return data


def append_jsonl(path: Path, rows: Iterable[ItemRow | VoteRow | LabelRow]) -> None:
    """Только дополняем: прерванный прогон продолжается с того же места."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")


def to_cases(
    data: Dataset, split: str, labels: str = "gold"
) -> tuple[list[Case], dict[str, BusinessProfile]]:
    """Набор для evals.run. labels: gold — человек или единогласие панели (для замера);
    human — только человек; author — метка автора (для подгонки на train)."""
    cases: list[Case] = []
    for item in sorted(data.items.values(), key=lambda i: i.id):
        if split != "all" and item.split != split:
            continue
        if not usable(item.code_violations):
            continue
        label: Label | None
        if labels == "gold":
            label, _ = gold(data, item.id)
        elif labels == "human":
            human = data.labels.get(item.id)
            label = human.label if human else None
        else:
            vote = data.votes.get(item.id, {}).get(AUTHOR)
            label = vote.label if vote else None
        if label is None:
            continue
        cases.append(Case(item.id, item.profile_id, item.headline, item.text, label, item.category))
    profiles = {case.profile: data.profiles[case.profile].business() for case in cases}
    return cases, profiles


def by_profile(items: Mapping[str, ItemRow]) -> dict[str, list[ItemRow]]:
    grouped: dict[str, list[ItemRow]] = {}
    for item in sorted(items.values(), key=lambda i: i.id):
        grouped.setdefault(item.profile_id, []).append(item)
    return grouped
