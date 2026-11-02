"""Профили и объявления, написанные вручную. Каждое объявление написано под вид из AD_KINDS,
нарушение в нём ровно одно, `span` — дословно нарушающий фрагмент. Метка автора — голос
`claude` в панели; правдой она становится только при согласии остальных разметчиков.

`label` переопределяет метку вида там, где автор сам считает случай пограничным.
"""

from dataclasses import dataclass

from adhelper.creatives.adapters.rules_file import load_rules
from adhelper.creatives.domain.model import AdDraft
from evals.dataset.build import Dataset, ItemRow, ProfileRow, VoteRow, code_violations, split_of
from evals.dataset.spec import AUTHOR, KINDS_BY_CATEGORY, SPLIT_SEED, Label

RULES = load_rules("creatives-2")


@dataclass(frozen=True)
class A:
    category: str
    headline: str
    text: str
    span: str = ""
    label: Label | None = None


@dataclass(frozen=True)
class P:
    id: str
    niche: str
    style: str
    name: str
    services: tuple[tuple[str, int | None], ...]
    address: str
    features: tuple[str, ...]
    ads: tuple[A, ...]


def batches() -> dict[str, tuple[P, ...]]:
    from evals.dataset.authored import a1_beauty, a1_city, a1_home, a2, a3, a4

    return {
        "a1": a1_beauty.PROFILES + a1_city.PROFILES + a1_home.PROFILES,
        "a2": a2.PROFILES,
        "a3": a3.PROFILES,
        "a4": a4.PROFILES,
    }


def build_authored(data: Dataset) -> None:
    for batch, profiles in batches().items():
        for p in profiles:
            row = ProfileRow(
                p.id, batch, p.niche, p.style, p.name, p.services, p.address, p.features
            )
            data.profiles[p.id] = row
            for number, ad in enumerate(p.ads, 1):
                violations = code_violations(AdDraft(ad.headline, ad.text), row.business(), RULES)
                item = ItemRow(
                    f"{p.id}-{number:02d}",
                    batch,
                    split_of(SPLIT_SEED, p.id),
                    p.id,
                    "authored",
                    ad.category,
                    ad.headline,
                    ad.text,
                    ad.span,
                    tuple(violations),
                    AUTHOR,
                    "authored",
                )
                data.items[item.id] = item
                label = ad.label or KINDS_BY_CATEGORY[ad.category].label
                data.add_vote(VoteRow(item.id, AUTHOR, label, ad.span, "authored", batch))
    # Голос автора по объявлениям писателя. Сами объявления читаются из writer.jsonl.
    from evals.dataset.authored.writer_votes import VOTES

    for item_id, (label, reason) in VOTES.items():
        data.add_vote(VoteRow(item_id, AUTHOR, label, reason, "authored", "writer"))
