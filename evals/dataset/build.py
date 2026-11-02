"""Чистые функции набора: разбор ответов, фильтры, выборки, хранение. Сеть не нужна.

Файлы в evals/data/:
- profiles.jsonl — выдуманные профили бизнесов;
- items.jsonl — объявления с предварительной меткой генератора;
- labels.jsonl — метки человека, только дополняется, последняя запись по id побеждает;
- manifest.json — версии, модели и счётчики каждого прогона.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from adhelper.creatives.domain.checks import check_draft
from adhelper.creatives.domain.model import (
    AdDraft,
    BusinessProfile,
    CreativeRules,
    Service,
    ViolationKind,
)
from evals.cases import Case
from evals.dataset.spec import KINDS_BY_CATEGORY, SPLITS, Label, SplitName

DATA = Path(__file__).resolve().parent.parent / "data"
LABELS: tuple[Label, ...] = ("pass", "block", "ambiguous")
# Такие черновики площадка не примет, до судьи они не доходят.
UNUSABLE = frozenset({ViolationKind.TOO_LONG, ViolationKind.EMPTY})
LATIN_WORD = re.compile(r"[a-z]{3,}", re.IGNORECASE)


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
    model: str

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
    # writer — настоящий промпт писателя из сервиса, метки нет до разметки человеком;
    # injected — генератор писал объявление заданного вида, метка предварительная.
    origin: str
    category: str
    proposed_label: Label | None
    headline: str
    text: str
    # Фрагмент, который генератор считает нарушением. Подсказка для разметчика.
    span: str
    code_violations: tuple[str, ...]
    model: str
    prompt_version: str


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
    labels: dict[str, LabelRow] = field(default_factory=dict)


# --- разбор ответов модели ---


def _loads_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError as error:
        raise BadReply("not_json") from error
    if not isinstance(data, dict):
        raise BadReply("not_object")
    return cast(dict[str, Any], data)


def _clean(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text or len(text) > limit:
        raise BadReply("bad_string")
    return text


def parse_profile(
    text: str,
) -> tuple[str, tuple[tuple[str, int | None], ...], str, tuple[str, ...]]:
    """Профиль от модели проверяет код: цены целые и разумные, полей не больше нужного."""
    data = _loads_object(text)
    name = _clean(data.get("name", ""), 40)
    raw_services = data.get("services")
    if not isinstance(raw_services, list) or not 2 <= len(raw_services) <= 5:
        raise BadReply("services_count")
    services: list[tuple[str, int | None]] = []
    for raw in raw_services:
        if not isinstance(raw, dict):
            raise BadReply("service_shape")
        price = raw.get("price")
        if price is not None and (
            isinstance(price, bool) or not isinstance(price, int) or not 50 <= price <= 500_000
        ):
            raise BadReply("price")
        services.append((_clean(raw.get("name", ""), 50), price))
    if all(price is None for _, price in services):
        raise BadReply("no_prices")
    raw_features = data.get("features")
    if not isinstance(raw_features, list) or not 2 <= len(raw_features) <= 4:
        raise BadReply("features_count")
    features = tuple(_clean(feature, 80) for feature in raw_features)
    address = str(data.get("address") or "").strip()[:60]
    if LATIN_WORD.search(" ".join([name, *features, *(s for s, _ in services)])):
        raise BadReply("latin")
    return name, tuple(services), address, features


def parse_ads(text: str, with_category: bool) -> list[tuple[str, str, str, str]]:
    """Возвращает (вид, заголовок, текст, фрагмент). Неизвестный вид отбрасывается."""
    data = _loads_object(text)
    ads = data.get("ads")
    if not isinstance(ads, list):
        raise BadReply("no_ads")
    result: list[tuple[str, str, str, str]] = []
    for ad in ads:
        if not isinstance(ad, dict):
            continue
        category = str(ad.get("category", "")) if with_category else "writer"
        if with_category and category not in KINDS_BY_CATEGORY:
            continue
        headline = str(ad.get("headline", "")).strip()
        body = str(ad.get("text", "")).strip()
        span = str(ad.get("span", "") or "").strip()[:120]
        result.append((category, headline, body, span))
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
    """Выборка зависит только от профиля и зерна: новый прогон не двигает старые записи."""
    value = bucket(seed, profile_id)
    edge = 0
    for name, share in SPLITS:
        edge += share
        if value < edge:
            return name
    raise AssertionError("доли выборок должны давать 100")


def pick_kinds(seed: str, profile_id: str, categories: Sequence[str], count: int) -> list[str]:
    """Детерминированное подмножество видов, чтобы не платить за все виды на каждом профиле."""
    ranked = sorted(categories, key=lambda c: bucket(seed, f"{profile_id}:{c}"))
    return sorted(ranked[:count])


def prompt_version(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:10]


# --- хранение ---


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(cast(dict[str, Any], json.loads(line)))
    return rows


def _profile(row: Mapping[str, Any]) -> ProfileRow:
    services = tuple((str(name), price) for name, price in row["services"])
    return ProfileRow(**{**row, "services": services, "features": tuple(row["features"])})


def _item(row: Mapping[str, Any]) -> ItemRow:
    return ItemRow(**{**row, "code_violations": tuple(row["code_violations"])})


def load(root: Path = DATA) -> Dataset:
    data = Dataset()
    for row in _read_jsonl(root / "profiles.jsonl"):
        profile = _profile(row)
        data.profiles[profile.id] = profile
    for row in _read_jsonl(root / "items.jsonl"):
        item = _item(row)
        data.items[item.id] = item
    for row in _read_jsonl(root / "labels.jsonl"):
        data.labels[str(row["id"])] = LabelRow(**row)
    return data


def append_jsonl(path: Path, rows: Iterable[ProfileRow | ItemRow | LabelRow]) -> None:
    """Только дополняем: прерванный прогон продолжается с того же места."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")


def to_cases(
    data: Dataset, split: str, labels: str = "human"
) -> tuple[list[Case], dict[str, BusinessProfile]]:
    """Набор для evals.run. labels: human — только размеченное человеком (для замера);
    proposed — метка человека, а без неё предварительная (для подгонки на train)."""
    cases: list[Case] = []
    for item in sorted(data.items.values(), key=lambda i: i.id):
        if split != "all" and item.split != split:
            continue
        if not usable(item.code_violations):
            continue
        human = data.labels.get(item.id)
        label = human.label if human else (item.proposed_label if labels == "proposed" else None)
        if label is None:
            continue
        cases.append(Case(item.id, item.profile_id, item.headline, item.text, label, item.category))
    profiles = {case.profile: data.profiles[case.profile].business() for case in cases}
    return cases, profiles
