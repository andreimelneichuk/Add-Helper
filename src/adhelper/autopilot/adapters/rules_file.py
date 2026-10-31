import tomllib
from importlib.resources import files

from adhelper.autopilot.domain.rules import AutopilotRules


def load_rules(version: str) -> AutopilotRules:
    resource = files("adhelper.autopilot.adapters") / "rules" / f"{version}.toml"
    return AutopilotRules(**tomllib.loads(resource.read_text(encoding="utf-8")))
