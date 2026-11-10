"""Подбор фото к объявлениям по смыслу: что видит модель, решает код (docs/05-creatives.md §3)."""

from adhelper.creatives.domain.photo_match import (
    GENERAL,
    UNFIT,
    Candidate,
    Match,
    match_drafts,
    read_subject,
    services_of,
)

SERVICES = ["Маникюр", "Стрижка"]


class Source:
    def __init__(self, key: str) -> None:
        self.key = key


def test_subject_is_read_only_from_the_closed_list() -> None:
    assert read_subject('{"subject": "Маникюр"}', SERVICES) == "Маникюр"
    assert read_subject('{"subject": "general"}', SERVICES) == GENERAL
    assert read_subject('{"subject": "unfit"}', SERVICES) == UNFIT
    # Выдуманная услуга, чужой ключ, не JSON и пустой ответ не превращаются в решение.
    assert read_subject('{"subject": "Татуировка"}', SERVICES) is None
    assert read_subject('{"услуга": "Маникюр"}', SERVICES) is None
    assert read_subject("Маникюр", SERVICES) is None
    assert read_subject("", SERVICES) is None
    assert read_subject('{"subject": ["Маникюр"]}', SERVICES) is None


def test_draft_services_come_from_its_fact_sources() -> None:
    sources = [Source("service:Маникюр"), Source("price:Маникюр:1500"), Source("service:Стрижка")]
    assert services_of(sources) == ("Маникюр", "Стрижка")
    assert services_of([Source("feature:Стерильно")]) == ()


def test_photo_goes_to_the_ad_of_its_own_service() -> None:
    photos = [Candidate(1, "Стрижка", True), Candidate(2, "Маникюр", True)]

    found = match_drafts([("Маникюр",), ("Стрижка",)], photos)

    assert found == [Match(2, None), Match(1, None)]


def test_least_used_fitting_photo_is_taken_and_owner_comes_first() -> None:
    photos = [
        Candidate(5, "Маникюр", False),
        Candidate(3, "Маникюр", True),
        Candidate(4, "Маникюр", True),
    ]

    found = match_drafts([("Маникюр",)] * 4, photos)

    # Сначала свои по порядку, потом чередуем; сделанное сервисом идёт после своих.
    assert [m.image_id for m in found] == [3, 4, 5, 3]


def test_scene_is_asked_for_a_service_without_its_own_photo_and_general_is_kept_spare() -> None:
    photos = [Candidate(1, GENERAL, True), Candidate(2, "Маникюр", True), Candidate(3, UNFIT, True)]

    found = match_drafts([("Маникюр",), ("Стрижка",)], photos)

    assert found == [Match(2, None), Match(None, "Стрижка", 1)]


def test_draft_without_a_service_takes_a_general_photo_or_nothing() -> None:
    assert match_drafts([()], [Candidate(1, GENERAL, True)]) == [Match(1, None)]
    assert match_drafts([()], [Candidate(3, UNFIT, True)]) == [Match(None, None)]


def test_missing_service_is_named_so_a_scene_can_be_made() -> None:
    found = match_drafts([("Педикюр", "Маникюр"), ()], [Candidate(3, UNFIT, True)])

    assert found == [Match(None, "Педикюр"), Match(None, None)]
