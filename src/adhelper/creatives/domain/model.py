from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ViolationKind(StrEnum):
    TOO_LONG = "too_long"
    EMPTY = "empty"
    UNCONFIRMED_NUMBER = "unconfirmed_number"
    UNCONFIRMED_CLAIM = "unconfirmed_claim"
    UNCONFIRMED_BY_JUDGE = "unconfirmed_by_judge"
    FORBIDDEN_WORD = "forbidden_word"
    FOREIGN_WORD = "foreign_word"
    TOO_SIMILAR = "too_similar"


class CreativeSource(StrEnum):
    MODEL = "model"
    TEMPLATE = "template"


class ModelUnavailable(Exception):
    """Модель не ответила, ответ обрезан или отклонён фильтром."""


@dataclass(frozen=True)
class Service:
    name: str
    price: int | None = None


@dataclass(frozen=True)
class BusinessProfile:
    """Только то, что можно показать в рекламе и передать модели. Реквизитов здесь нет."""

    name: str
    services: tuple[Service, ...]
    address: str = ""
    features: tuple[str, ...] = ()
    niche: str = ""


@dataclass(frozen=True)
class AdDraft:
    headline: str
    text: str


@dataclass(frozen=True)
class Violation:
    kind: ViolationKind
    detail: str


@dataclass(frozen=True)
class RejectedDraft:
    draft: AdDraft
    violations: tuple[Violation, ...]


@dataclass(frozen=True)
class CreativeRules:
    version: str
    headline_max: int
    text_max: int
    narrow_max: int
    narrow_chars: str
    similarity_max_bp: int
    max_tokens_per_set: int
    max_model_attempts: int
    judge_max_tokens: int
    min_contrast_x100: int
    forbidden_words: tuple[str, ...]
    fact_words: tuple[str, ...]
    # Начала слов-обещаний: «скидк» ловит «скидкой», «бесплатн» узнаёт «бесплатно» в профиле.
    fact_stems: tuple[str, ...]
    sensitive_niches: tuple[str, ...]
    # Сколько наборов бизнес может запросить за сутки: каждый стоит вызовов модели.
    max_sets_per_day: int


@dataclass(frozen=True)
class CreativeSet:
    drafts: tuple[AdDraft, ...]
    rejected: tuple[RejectedDraft, ...]
    source: CreativeSource
    tokens_used: int
    rules_version: str
    needs_review: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ModelReply:
    text: str
    tokens: int


class TextModel(Protocol):
    async def complete_json(
        self, system: str, user: str, schema: dict[str, object], max_tokens: int
    ) -> ModelReply: ...


class SetStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class DraftDecision(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    DECLINED = "declined"


@dataclass(frozen=True)
class StoredDraft:
    headline: str
    text: str
    decision: DraftDecision


@dataclass(frozen=True)
class StoredRejection:
    headline: str
    text: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class StoredSet:
    """Набор объявлений в том виде, в каком его видит владелец."""

    id: int
    status: SetStatus
    rules_version: str
    source: CreativeSource | None
    drafts: tuple[StoredDraft, ...]
    rejected: tuple[StoredRejection, ...]
    notes: tuple[str, ...]
    tokens_used: int
    needs_review: bool
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class ClaimedSet:
    id: int
    business_id: UUID
    rules_version: str
