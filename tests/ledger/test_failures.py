from adhelper.budget.domain.limits import RejectionReason
from adhelper.ledger.domain.failures import DEFAULT_FAILURE, FAILURE_TEXT, failure_text


def test_every_budget_rejection_has_an_owner_text() -> None:
    for reason in RejectionReason:
        assert reason.value in FAILURE_TEXT, reason


def test_known_codes_give_their_own_text() -> None:
    assert failure_text("exceeds_operating_budget") == FAILURE_TEXT["exceeds_operating_budget"]


def test_unknown_code_gets_the_general_text_not_the_raw_code() -> None:
    text = failure_text("some_new_code")
    assert text == DEFAULT_FAILURE
    assert "some_new_code" not in text


def test_no_code_means_no_text() -> None:
    assert failure_text(None) is None
    assert failure_text("") is None


def test_texts_never_show_raw_codes_or_english() -> None:
    for code, text in FAILURE_TEXT.items():
        assert code not in text
        assert "_" not in text, code


def test_default_text_is_the_whole_sentence() -> None:
    # Раньше строка резалась по отсутствию скобок: владелец видел обрыв без «позже».
    text = failure_text("some_new_code")
    assert text is not None
    assert text.endswith("попробуйте позже")
