"""Данные рекламодателя для маркировки (docs/03-integrations.md §4).

В Директ они уходят при запуске (`Clients.update`), до этого только хранятся зашифрованными.
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class AdvertiserKind(StrEnum):
    LEGAL = "legal"  # организация, ИНН из 10 цифр
    IP = "ip"  # индивидуальный предприниматель, 12 цифр
    PERSON = "person"  # физическое лицо, 12 цифр и телефон


class MarkingProblem(StrEnum):
    INN_FORMAT = "inn_format"
    INN_CHECKSUM = "inn_checksum"
    INN_LENGTH_FOR_KIND = "inn_length_for_kind"
    NAME = "name"
    PHONE = "phone"
    OKVED = "okved"


class SharedAccountMarkingMismatch(Exception):
    """Бизнесы одного кабинета Директа должны иметь одного рекламодателя."""


@dataclass(frozen=True)
class Marking:
    kind: AdvertiserKind
    inn: str
    name: str
    phone: str | None
    # Организации и ИП нужен ОКВЭД, физическому лицу нет (docs/00-research.md §5).
    okved: str | None = None


INN_LENGTH = {AdvertiserKind.LEGAL: 10, AdvertiserKind.IP: 12, AdvertiserKind.PERSON: 12}
MAX_NAME = 300
_W10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_W11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_W12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN = re.compile(r"\d{10}|\d{12}")
# 96.02 или 47.11.1: класс, подкласс, группа. Принимает ли Директ код короче группы, не проверено.
_OKVED = re.compile(r"\d{2}\.\d{1,2}(\.\d{1,2})?")


def _check_digit(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights, strict=False)) % 11 % 10


def inn_is_valid(inn: str) -> bool:
    """Контрольные цифры ИНН по алгоритму ФНС."""
    if not _INN.fullmatch(inn):
        return False
    if len(inn) == 10:
        return _check_digit(inn, _W10) == int(inn[9])
    return _check_digit(inn, _W11) == int(inn[10]) and _check_digit(inn, _W12) == int(inn[11])


def normalize_phone(raw: str) -> str | None:
    """Российский номер в виде +7XXXXXXXXXX или None."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    return None


def check_marking(marking: Marking) -> MarkingProblem | None:
    if not _INN.fullmatch(marking.inn):
        return MarkingProblem.INN_FORMAT
    if len(marking.inn) != INN_LENGTH[marking.kind]:
        return MarkingProblem.INN_LENGTH_FOR_KIND
    if not inn_is_valid(marking.inn):
        return MarkingProblem.INN_CHECKSUM
    if not marking.name.strip() or len(marking.name) > MAX_NAME:
        return MarkingProblem.NAME
    if marking.kind is AdvertiserKind.PERSON and (
        marking.phone is None or normalize_phone(marking.phone) is None
    ):
        return MarkingProblem.PHONE
    if marking.kind is not AdvertiserKind.PERSON and not _OKVED.fullmatch(marking.okved or ""):
        return MarkingProblem.OKVED
    return None


def mask_inn(inn: str) -> str:
    return "•" * (len(inn) - 4) + inn[-4:]
