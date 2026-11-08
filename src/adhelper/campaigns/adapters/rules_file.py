import tomllib
from importlib.resources import files

from adhelper.campaigns.domain.launch import LaunchRules
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


CURRENT_LAUNCH_RULES = "launch-1"


def load_launch_rules(version: str = CURRENT_LAUNCH_RULES) -> LaunchRules:
    resource = files("adhelper.campaigns.adapters") / "rules" / f"{version}.toml"
    raw = tomllib.loads(resource.read_text(encoding="utf-8"))
    return LaunchRules(
        version=raw["version"],
        period_days=raw["period_days"],
        min_daily_rubles=raw["min_daily_rubles"],
        split_from_monthly_rubles=raw["split_from_monthly_rubles"],
        max_ads_per_campaign=raw["max_ads_per_campaign"],
        cities={str(name): int(region) for name, region in raw["cities"].items()},
        fallback_region_id=(
            int(raw["fallback_region_id"]) if raw.get("fallback_region_id") is not None else None
        ),
    )
