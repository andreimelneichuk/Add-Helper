"""Расчёт ворот выпуска без новой разметки (docs/12-agents.md §2). Сеть не нужна."""

import pytest
from evals import gate
from evals.gate import (
    GateInput,
    _by_text,
    _key,
    _passed,
    launched_error,
    prevalence,
    verdict,
)
from hypothesis import given
from hypothesis import strategies as st


def test_prevalence_corrects_for_known_judge_errors() -> None:
    # Судья пропускает 10% нарушений и зря снимает 5% чистых. Настоящая доля выдумок 20%:
    # видимая доля снятых 0.2 * 0.9 + 0.8 * 0.05 = 0.22.
    assert prevalence(0.22, 0.10, 0.05) == pytest.approx(0.20)


def test_prevalence_is_clipped() -> None:
    assert prevalence(0.01, 0.10, 0.05) == 0.0
    assert prevalence(0.99, 0.60, 0.50) == 1.0


def test_launched_error_is_misses_among_passed() -> None:
    # 20% выдумок, пропуск 10%, зря снято 5%: прошло 0.02 выдумок и 0.76 чистых.
    assert launched_error(0.20, 0.10, 0.05) == pytest.approx(0.02 / 0.78)


def test_example_from_docs_passes() -> None:
    # Пропуск 0 из 114 нарушений эталона, 8 из 300 чистых сняты зря, у писателя снято 35 из 316.
    result = verdict(GateInput(35, 316, 0, 114, 8, 300))
    assert result.passed
    assert result.error_upper < 0.01


def test_judge_that_misses_a_lot_fails() -> None:
    result = verdict(GateInput(35, 316, 20, 114, 8, 300))
    assert not result.passed


def test_tiny_reference_set_fails() -> None:
    # Без достаточного эталона граница пропуска широкая, ворота закрыты.
    assert not verdict(GateInput(35, 316, 0, 10, 0, 30)).passed


@given(
    st.integers(0, 300),
    st.integers(0, 50),
    st.integers(0, 50),
)
def test_more_misses_never_help(blocked: int, misses: int, false_blocks: int) -> None:
    base = verdict(GateInput(blocked, 300, misses, 114, false_blocks, 300))
    worse = verdict(GateInput(blocked, 300, misses + 1, 114, false_blocks, 300))
    assert worse.error_upper >= base.error_upper


def test_many_false_blocks_do_not_hide_violations() -> None:
    # Если на эталоне судья часто снимает зря, точная поправка дала бы v = 0 и открыла ворота
    # при любом пропуске. Граница v ≤ b / (1 − m) от f не зависит.
    assert not verdict(GateInput(30, 329, 14, 114, 58, 346)).passed


def test_old_cache_is_keyed_by_ad_text() -> None:
    # Прежний кеш хранил решения списком по проверенным объявлениям; после пересчёта кода
    # список сдвинулся бы, поэтому решения переводятся в словарь по тексту.
    a = {"headline": "А", "text": "1"}
    b = {"headline": "Б", "text": "2"}
    old = {"p": [{"blocked": False, "reason": ""}, {"blocked": True, "reason": "выдумка"}]}
    keyed = _by_text(old, {"p": [a, b]})
    assert keyed["p"][_key(b)]["blocked"]
    # Код теперь снимает «А»: решение по «Б» остаётся при своём объявлении.
    assert _passed({"p": [b]}, keyed) == {"p": []}
    # Набор без решения хотя бы по одному объявлению в запуск не идёт.
    assert _passed({"p": [a, {"headline": "В", "text": "3"}]}, keyed) == {}


def test_openrouter_key_comes_from_settings_when_env_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ключ OpenRouter сервиса лежит в TEXT_MODEL_API_KEY: второй ключ для ворот не нужен.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "sk-or-test")
    assert gate.openrouter_key() == "sk-or-test"


def test_service_key_of_another_provider_is_not_sent_to_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://ai.api.cloud.yandex.net/v1")
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "yandex-key")
    assert gate.openrouter_key() == ""


def test_sample_takes_first_profiles_in_order() -> None:
    assert gate.sample_profiles({"c": 3, "a": 1, "b": 2}, 2) == {"a": 1, "b": 2}
    assert gate.sample_profiles({"c": 3, "a": 1}, 0) == {"a": 1, "c": 3}
