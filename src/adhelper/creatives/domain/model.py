from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ViolationKind(StrEnum):
    TOO_LONG = "too_long"
    EMPTY = "empty"
    UNCONFIRMED_NUMBER = "unconfirmed_number"
    UNCONFIRMED_CLAIM = "unconfirmed_claim"
    UNCONFIRMED_BY_JUDGE = "unconfirmed_by_judge"
    # Ссылка писателя на факт неверна: нет ключа, фрагмента или число без факта.
    UNBOUND_CLAIM = "unbound_claim"
    FORBIDDEN_WORD = "forbidden_word"
    FOREIGN_WORD = "foreign_word"
    TOO_SIMILAR = "too_similar"


class CreativeSource(StrEnum):
    MODEL = "model"
    TEMPLATE = "template"


class ModelUnavailable(Exception):
    """Модель не ответила, ответ обрезан или отклонён фильтром.

    `tokens` — сколько токенов ответ всё же потратил: обрезанный ответ оплачен.
    """

    def __init__(self, reason: str = "", tokens: int = 0) -> None:
        super().__init__(reason)
        self.tokens = tokens


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
    # Рамка писателя (creatives-3): пусто — проверяются только ключи, фрагменты и числа.
    fact_glue: tuple[str, ...] = ()
    claim_stems: tuple[str, ...] = ()


@dataclass(frozen=True)
class CreativeSet:
    drafts: tuple[AdDraft, ...]
    rejected: tuple[RejectedDraft, ...]
    source: CreativeSource
    tokens_used: int
    rules_version: str
    needs_review: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
    # Текст модели прошёл судью: только такой можно запускать без владельца.
    judged: bool = False


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


@dataclass(frozen=True)
class Rework:
    """Что переделываем: объявление площадки, его текст сейчас и просьба владельца."""

    ad_id: str
    base: AdDraft
    wish: str
    # Замена после правки факта: просьба наша, малая правка допустима, нужен один вариант.
    auto: bool = False


@dataclass(frozen=True)
class ClaimedSet:
    id: int
    # У набора мастера до входа бизнеса ещё нет.
    business_id: UUID | None
    rules_version: str
    # Место, для которого пишем. У наборов до §8 и у наборов мастера его нет.
    account: str | None = None
    campaign_id: str | None = None
    campaign_name: str | None = None
    # Факты черновика мастера: пишем по ним, а не по профилю бизнеса.
    profile: BusinessProfile | None = None
    # Переделка одного объявления по просьбе владельца.
    rework: Rework | None = None
    rework_rules: str | None = None


@dataclass(frozen=True)
class WizardRules:
    """Объявления в мастере до входа: лимиты гостя и размер черновика профиля."""

    version: str
    max_guest_sets_per_address: int
    max_guest_sets_per_day: int
    max_services: int
    max_features: int
    max_fact_chars: int
