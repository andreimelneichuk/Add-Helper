"""Расчёт ворот выпуска без новой разметки (docs/12-agents.md §2). Сеть не нужна."""

import pytest
from evals.gate import GateInput, launched_error, prevalence, verdict
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
