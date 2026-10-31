import tomllib
from importlib.resources import files

from adhelper.budget.domain.rules import BudgetRules


def load_rules(version: str) -> BudgetRules:
    resource = files("adhelper.budget.adapters") / "rules" / f"{version}.toml"
    return BudgetRules(**tomllib.loads(resource.read_text(encoding="utf-8")))
