import tomllib
from importlib.resources import files

from adhelper.stats.domain.today import TodayRules


def load_today_rules(version: str) -> TodayRules:
    resource = files("adhelper.stats.adapters") / "rules" / f"{version}.toml"
    raw = tomllib.loads(resource.read_text(encoding="utf-8"))
    return TodayRules(
        version=raw["version"],
        max_cards=raw["max_cards"],
        last_action_days=raw["last_action_days"],
        unknown_alert=raw["unknown_alert"],
        priority=dict(raw["priority"]),
    )
