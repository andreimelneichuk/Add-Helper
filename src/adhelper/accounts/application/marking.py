"""Маркировка: проверка до сохранения, показ без полного ИНН."""

from typing import Protocol

from adhelper.accounts.domain.marking import (
    AdvertiserKind,
    Marking,
    MarkingProblem,
    check_marking,
    mask_inn,
    normalize_phone,
)

__all__ = [
    "AdvertiserKind",
    "Marking",
    "MarkingProblem",
    "MarkingStore",
    "masked",
    "save_marking",
]


class MarkingStore(Protocol):
    async def get(self) -> Marking | None: ...
    async def save(self, marking: Marking) -> None: ...


async def save_marking(store: MarkingStore, marking: Marking) -> MarkingProblem | None:
    problem = check_marking(marking)
    if problem is not None:
        return problem
    phone = normalize_phone(marking.phone) if marking.phone else None
    okved = None if marking.kind is AdvertiserKind.PERSON else marking.okved
    await store.save(Marking(marking.kind, marking.inn, marking.name.strip(), phone, okved))
    return None


def masked(marking: Marking) -> dict[str, str | None]:
    phone = marking.phone
    return {
        "kind": marking.kind.value,
        "inn": mask_inn(marking.inn),
        "name": marking.name,
        "phone": None if phone is None else f"+7 ••• •••-{phone[-4:-2]}-{phone[-2:]}",
        "okved": marking.okved,
    }
