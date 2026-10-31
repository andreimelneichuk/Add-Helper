import pytest

from adhelper.budget.adapters.rules_file import load_rules


def test_loads_versioned_rules() -> None:
    rules = load_rules("budget-1")

    assert rules.version == "budget-1"
    assert rules.reserve_bp == 1000
    assert rules.stop_threshold_bp == 9700


def test_unknown_version_is_an_error() -> None:
    with pytest.raises(FileNotFoundError):
        load_rules("budget-999")
