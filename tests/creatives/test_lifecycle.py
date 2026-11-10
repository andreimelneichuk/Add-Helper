"""Места для объявлений и поводы писать новые: решает код, а не модель и не владелец."""

from adhelper.creatives.domain.lifecycle import (
    CampaignSlots,
    Candidate,
    CreativeState,
    Request,
    SlotPlan,
    SlotRules,
    WriteReason,
    ad_basis,
    fact_sources,
    is_stale,
    plan_slots,
)
from adhelper.creatives.domain.model import AdDraft, BusinessProfile, Service

PROFILE = BusinessProfile(
    "Лак",
    (Service("Маникюр с покрытием", 1500), Service("Педикюр", None)),
    "ул. Ленина, 5",
    ("Стерильные инструменты из крафт-пакета",),
    "маникюр",
)
RULES = SlotRules(version="ads-1", slots_per_campaign=3, reserve_max=2, similarity_max_bp=7000)
PRICE = AdDraft("Маникюр с покрытием 1500 ₽", "Стерильные инструменты из крафт-пакета.")
NEAR = AdDraft("Педикюр рядом с метро", "Ждём вас: ул. Ленина, 5. Запись онлайн.")
NAME = AdDraft("Педикюр в «Лак»", "Ул. Ленина, 5. Запишитесь онлайн.")


def campaign(
    live: tuple[AdDraft, ...] = (), running: bool = True, ever: bool = True
) -> CampaignSlots:
    return CampaignSlots("lak", "101", running=running, live=live, ever_had_ads=ever)


def candidate(id: int, state: CreativeState, draft: AdDraft, auto_ok: bool = True) -> Candidate:
    return Candidate(
        id, "lak", "101", state, draft, auto_ok, tuple(s.key for s in fact_sources(draft, PROFILE))
    )


def plan(
    campaigns: list[CampaignSlots],
    candidates: list[Candidate],
    open_sets: frozenset[tuple[str, str]] = frozenset(),
    sets_left: int = 5,
    sensitive: bool = False,
    owner_decides: bool = False,
) -> SlotPlan:
    return plan_slots(
        PROFILE, campaigns, candidates, RULES, open_sets, sets_left, sensitive, owner_decides
    )


def test_fact_sources_name_profile_fields() -> None:
    sources = {s.fragment: s.label for s in fact_sources(PRICE, PROFILE)}
    assert sources["1500 ₽"] == "цена услуги «Маникюр с покрытием»"
    assert sources["Маникюр с покрытием"] == "услуга из профиля"
    assert sources["Стерильные инструменты из крафт-пакета"] == "особенность из профиля"
    address = {s.fragment for s in fact_sources(NEAR, PROFILE)}
    assert "ул. Ленина, 5" in address


def test_new_campaign_gets_three_ads() -> None:
    result = plan([campaign(ever=False)], [])
    assert result.requests == (Request("lak", "101", WriteReason.NEW_CAMPAIGN, 3),)


def test_free_place_is_filled_from_ready_then_reserve_keeps_the_rest() -> None:
    result = plan(
        [campaign(live=(NEAR,))],
        [
            candidate(1, CreativeState.READY, PRICE),
            candidate(
                2,
                CreativeState.READY,
                AdDraft("Педикюр рядом с метро", "Ждём вас: ул. Ленина, 5. Запись онлайн!"),
            ),
            candidate(
                3,
                CreativeState.READY,
                AdDraft("Педикюр", "Запишитесь онлайн: ул. Ленина, 5. Стерильные инструменты."),
            ),
        ],
    )
    moves = {m.creative_id: m.state for m in result.moves}
    assert moves[1] is CreativeState.LAUNCH_PENDING
    # Второе слово в слово повторяет работающее объявление.
    assert moves[2] is CreativeState.STOPPED
    assert moves[3] is CreativeState.LAUNCH_PENDING
    assert result.requests == ()


def test_lost_or_rejected_ad_frees_a_place_and_a_writer_is_asked() -> None:
    result = plan([campaign(live=(PRICE, NEAR))], [])
    assert result.requests == (Request("lak", "101", WriteReason.REFILL, 1),)


def test_one_set_at_a_time_and_daily_limit() -> None:
    assert plan([campaign(live=(PRICE,))], [], open_sets=frozenset({("lak", "101")})).requests == ()
    assert plan([campaign(live=(PRICE,))], [], sets_left=0).requests == ()


def test_paused_campaign_is_left_alone() -> None:
    result = plan([campaign(running=False)], [candidate(1, CreativeState.READY, PRICE)])
    assert result.moves == () and result.requests == ()


def test_changed_price_stops_waiting_ads_and_asks_for_replacement() -> None:
    cheaper = BusinessProfile(
        PROFILE.name,
        (Service("Маникюр с покрытием", 1700),),
        PROFILE.address,
        PROFILE.features,
        "маникюр",
    )
    waiting = candidate(1, CreativeState.LAUNCH_PENDING, PRICE)
    assert is_stale(waiting, cheaper)
    result = plan_slots(
        cheaper, [campaign(live=(NEAR, NAME))], [waiting], RULES, frozenset(), 5, False, False
    )
    assert [(m.creative_id, m.state, m.reason) for m in result.moves] == [
        (1, CreativeState.STOPPED, "fact_changed")
    ]
    assert result.requests == (Request("lak", "101", WriteReason.FACT_CHANGED, 1),)


def test_removed_feature_makes_ad_stale_even_without_numbers() -> None:
    plain = BusinessProfile(PROFILE.name, PROFILE.services, PROFILE.address, (), "маникюр")
    assert is_stale(candidate(1, CreativeState.READY, PRICE), plain)
    assert not is_stale(candidate(2, CreativeState.READY, NEAR), plain)


def test_sensitive_niche_or_unjudged_text_waits_for_owner() -> None:
    result = plan(
        [campaign(live=(NEAR, NAME))], [candidate(1, CreativeState.READY, PRICE)], sensitive=True
    )
    assert result.moves[0].state is CreativeState.NEEDS_OWNER
    assert "ads_need_confirmation" in result.attention
    unjudged = plan(
        [campaign(live=(NEAR, NAME))], [candidate(1, CreativeState.READY, PRICE, auto_ok=False)]
    )
    assert unjudged.moves[0].state is CreativeState.NEEDS_OWNER


def test_new_ad_waits_for_owner_unless_autopilot_acts_on_its_own() -> None:
    """Режим автопилота решает, уходит ли новое объявление на место само (docs/04, docs/05 §8)."""
    ready = [candidate(1, CreativeState.READY, PRICE)]
    asked = plan([campaign(live=(NEAR, NAME))], ready, owner_decides=True)
    assert asked.moves[0].state is CreativeState.NEEDS_OWNER
    assert "ads_need_confirmation" in asked.attention
    own = plan([campaign(live=(NEAR, NAME))], ready, owner_decides=False)
    assert own.moves[0].state is CreativeState.LAUNCH_PENDING


def test_waiting_for_owner_takes_a_place() -> None:
    waiting = candidate(1, CreativeState.NEEDS_OWNER, PRICE)
    result = plan([campaign(live=(NEAR, NAME))], [waiting], sensitive=True)
    assert result.requests == () and "ads_need_confirmation" in result.attention


def test_empty_profile_asks_owner_and_writes_nothing() -> None:
    empty = BusinessProfile("Лак", (), "", (), "")
    result = plan_slots(empty, [campaign(ever=False)], [], RULES, frozenset(), 5, False, False)
    assert result.requests == () and result.attention == ("ads_no_profile",)


def test_reserve_is_capped() -> None:
    drafts = [
        AdDraft("Педикюр", "Запишитесь онлайн."),
        AdDraft("Маникюр с покрытием", "Ул. Ленина, 5."),
        AdDraft("Педикюр в «Лак»", "Стерильные инструменты из крафт-пакета."),
        AdDraft("Маникюр с покрытием за 1500 ₽", "Ждём вас."),
    ]
    extra = [candidate(i, CreativeState.READY, d) for i, d in enumerate(drafts, start=1)]
    result = plan([campaign(live=(PRICE, NEAR, NAME))], extra)
    states = [m.state for m in result.moves]
    assert states.count(CreativeState.RESERVE) == 2
    assert states.count(CreativeState.STOPPED) == 2


def test_basis_flags_numbers_missing_from_profile() -> None:
    basis = ad_basis(AdDraft("Маникюр за 990 ₽", "Ул. Ленина, 5."), PROFILE)
    assert basis.unconfirmed == ("990",)
    assert "ул. Ленина, 5" in {s.fragment for s in basis.sources}
    assert ad_basis(PRICE, None).unconfirmed == ("1500",)
