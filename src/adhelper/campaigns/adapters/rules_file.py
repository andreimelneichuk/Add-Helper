import tomllib
from importlib.resources import files

from adhelper.campaigns.domain.targeting import TargetingRules

CURRENT_TARGETING_RULES = "targeting-1"


def load_targeting_rules(version: str = CURRENT_TARGETING_RULES) -> TargetingRules:
    resource = files("adhelper.campaigns.adapters") / "rules" / f"{version}.toml"
    raw = tomllib.loads(resource.read_text(encoding="utf-8"))
    return TargetingRules(
        version=raw["version"],
        min_weekly_hours=raw["min_weekly_hours"],
        regions={int(key): str(name) for key, name in raw["regions"].items()},
    )
