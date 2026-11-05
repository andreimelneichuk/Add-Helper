import tomllib
from importlib.resources import files

from adhelper.budget.domain.plan import PlanRules

CURRENT_PLAN_RULES = "plan-2"


def load_plan_rules(version: str = CURRENT_PLAN_RULES) -> PlanRules:
    resource = files("adhelper.budget.adapters") / "rules" / f"{version}.toml"
    raw = tomllib.loads(resource.read_text(encoding="utf-8"))
    return PlanRules(
        version=raw["version"],
        min_monthly_rubles=raw["min_monthly_rubles"],
        max_monthly_rubles=raw["max_monthly_rubles"],
        presets=tuple(raw["presets"]),
    )
