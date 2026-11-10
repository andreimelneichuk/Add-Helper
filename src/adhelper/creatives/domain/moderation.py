"""Проверка объявлений Яндексом

Код решает, что делать с вердиктом: одобренное начинает показы, отклонённое получает ремонт
не более `max_repairs` раз, потом просьбу владельцу. Причину отказа площадка пишет текстом;
это данные, поэтому код сводит их к закрытому набору, и писатель получает просьбу из правил,
а не текст площадки.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from adhelper.creatives.domain.model import AdDraft
from adhelper.platforms.domain.ports import ModerationState, ModerationVerdict

# Метка объявления на площадке: по ней повтор находит созданное и не создаёт второе.
MARKER_PREFIX = "ahc-"


def ad_marker(creative_id: int) -> str:
    return f"{MARKER_PREFIX}{creative_id}"


@dataclass(frozen=True)
class RejectionKind:
    code: str
    # Начала слов в причине отказа, по которым узнаём вид. Ищем без учёта регистра.
    stems: tuple[str, ...]
    # Можно ли исправить текстом объявления. Сайт или документы текст не исправит.
    repairable: bool
    # Просьба писателю: её пишут правила, а не площадка.
    wish: str
    # Что сказать владельцу его словами.
    owner_text: str


@dataclass(frozen=True)
class ModerationRules:
    version: str
    max_repairs: int
    # Последний в списке — запасной вид, когда ни одно слово не подошло.
    kinds: tuple[RejectionKind, ...]

    def __post_init__(self) -> None:
        if not self.kinds or self.kinds[-1].stems:
            raise ValueError("Последний вид отказа должен быть запасным, без слов")


def classify(reasons: Sequence[str], rules: ModerationRules) -> RejectionKind:
    text = " ".join(reasons).lower().replace("ё", "е")
    for kind in rules.kinds:
        if any(stem in text for stem in kind.stems):
            return kind
    return rules.kinds[-1]


@dataclass(frozen=True)
class Checking:
    """Объявление площадки, которое ждёт итога проверки."""

    account: str
    campaign_id: str
    ad_id: str
    draft: AdDraft
    # Объявление, написанное сервисом. У объявлений из мастера строки ещё нет.
    creative_id: int | None
    # Какое объявление площадки заменяет; None — своё место в направлении.
    replaces: str | None
    # Сколько раз эту цепочку уже чинили после отказа.
    repairs: int


@dataclass(frozen=True)
class Rejection:
    ad: Checking
    kind: RejectionKind
    # Ремонт: следующая попытка цепочки. None — дальше решает владелец.
    repair: int | None


@dataclass(frozen=True)
class ModerationPlan:
    accepted: tuple[Checking, ...]
    rejected: tuple[Rejection, ...]

    @property
    def to_owner(self) -> tuple[Rejection, ...]:
        return tuple(r for r in self.rejected if r.repair is None)


def plan_moderation(
    checking: Sequence[Checking],
    verdicts: Sequence[ModerationVerdict],
    last_rejection: Mapping[tuple[str, str], str],
    rules: ModerationRules,
) -> ModerationPlan:
    """Что сделать по итогам проверки. Повтор безопасен: план сам ничего не меняет.

    Владелец нужен, когда текст не исправить, ремонты исчерпаны или направление дважды
    подряд отклонено по одной причине: тогда дело скорее в сайте или профиле.
    """
    by_id = {verdict.ad_id: verdict for verdict in verdicts}
    last = dict(last_rejection)
    accepted: list[Checking] = []
    rejected: list[Rejection] = []
    for ad in checking:
        verdict = by_id.get(ad.ad_id)
        if verdict is None or verdict.state is ModerationState.MODERATION:
            continue
        if verdict.state is ModerationState.ACCEPTED:
            accepted.append(ad)
            continue
        kind = classify(verdict.reasons, rules)
        key = (ad.account, ad.campaign_id)
        repeated = last.get(key) == kind.code
        last[key] = kind.code
        can_repair = kind.repairable and not repeated and ad.repairs < rules.max_repairs
        rejected.append(Rejection(ad, kind, ad.repairs + 1 if can_repair else None))
    return ModerationPlan(tuple(accepted), tuple(rejected))
